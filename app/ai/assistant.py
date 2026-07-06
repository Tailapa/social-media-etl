"""The AI assistant: answers questions using retrieved records and/or
generated SQL, always grounded (never hallucinated), always cited, and
always logged (conversation, message, query log, assistant log) — see
success criteria #11 and #13.
"""

from __future__ import annotations

import time
from dataclasses import asdict

from openai import AsyncOpenAI

from app.ai.sql_generator import SQLGenerator
from app.config import get_settings
from app.logging import get_logger
from app.models.pydantic import AssistantLog, ChatMessage, Conversation, QueryLog
from app.models.pydantic.enums import MessageRole
from app.prompts import (
    ASSISTANT_SYSTEM_PROMPT,
    CONVERSATION_MEMORY_PROMPT,
    CROSS_PLATFORM_COMPARISON_PROMPT,
    SENTIMENT_ANALYSIS_PROMPT,
    SUMMARIZATION_PROMPT,
    TREND_ANALYSIS_PROMPT,
)
from app.repositories.conversation_repository import ConversationRepository
from app.repositories.message_repository import MessageRepository
from app.repositories.query_log_repository import AssistantLogRepository, QueryLogRepository
from app.retrieval import RetrievalFilters, RetrievalService
from app.utils.exceptions import AssistantError

logger = get_logger(__name__)

_MAX_CONTEXT_CHARS_PER_RECORD = 500
_MAX_RETRIEVED_RECORDS = 8
_MAX_SQL_ROWS_IN_CONTEXT = 20
_MAX_HISTORY_TURNS = 6

# Cheap keyword-based intent detection: picks a specialized analysis-style
# prompt template (app.prompts) to supplement the generic system prompt when
# a question clearly asks for one of these analysis styles. Deliberately not
# an LLM-based classifier — a second model call to classify intent for every
# question would double latency/cost for a hint that's genuinely this easy
# to approximate with keywords.
_INTENT_KEYWORDS: dict[str, tuple[str, ...]] = {
    "trend": ("trend", "trending", "over time", "time-series", "timeseries"),
    "sentiment": ("sentiment", "feel about", "opinion", "controversial"),
    "comparison": ("compare", "comparison", " vs ", "versus", "cross-platform"),
    "summary": ("summarize", "summary", "sum up", "tl;dr"),
}


def _style_prompt_for(question: str, context_text: str) -> str | None:
    """Return a specialized analysis-style system prompt for `question`, if
    its wording matches one of the supported analysis intents, else `None`.
    """
    lowered = question.lower()
    if any(k in lowered for k in _INTENT_KEYWORDS["trend"]):
        return TREND_ANALYSIS_PROMPT.format(data=context_text)
    if any(k in lowered for k in _INTENT_KEYWORDS["sentiment"]):
        return SENTIMENT_ANALYSIS_PROMPT.format(content=context_text)
    if any(k in lowered for k in _INTENT_KEYWORDS["comparison"]):
        return CROSS_PLATFORM_COMPARISON_PROMPT.format(platform_data=context_text)
    if any(k in lowered for k in _INTENT_KEYWORDS["summary"]):
        return SUMMARIZATION_PROMPT.format(content=context_text)
    return None


def _filters_to_json(filters: RetrievalFilters | None) -> dict:
    """JSON-safe dict of applied filters for `query_logs.filters_applied` —
    `RetrievalFilters` may carry `datetime` values that need isoformatting.
    """
    if filters is None:
        return {}
    return {
        key: value.isoformat() if hasattr(value, "isoformat") else value
        for key, value in asdict(filters).items()
        if value is not None
    }


class Assistant:
    def __init__(
        self,
        retrieval: RetrievalService | None = None,
        sql_generator: SQLGenerator | None = None,
        client: AsyncOpenAI | None = None,
        model: str | None = None,
        conversation_repo: ConversationRepository | None = None,
        message_repo: MessageRepository | None = None,
        query_log_repo: QueryLogRepository | None = None,
        assistant_log_repo: AssistantLogRepository | None = None,
    ) -> None:
        settings = get_settings()
        self.retrieval = retrieval or RetrievalService()
        self.sql_generator = sql_generator or SQLGenerator()
        self.client = client or AsyncOpenAI(api_key=settings.openai_api_key.get_secret_value())
        self.model = model or settings.openai_chat_model
        self.conversation_repo = conversation_repo or ConversationRepository()
        self.message_repo = message_repo or MessageRepository()
        self.query_log_repo = query_log_repo or QueryLogRepository()
        self.assistant_log_repo = assistant_log_repo or AssistantLogRepository()

    async def ask(
        self,
        question: str,
        *,
        conversation_id: str | None = None,
        filters: RetrievalFilters | None = None,
    ) -> ChatMessage:
        """Answer `question`, persisting the full turn (user message,
        assistant message, query log, assistant log) regardless of whether
        SQL generation or retrieval succeeded.
        """
        start = time.perf_counter()

        if conversation_id is None:
            conversation = await self.conversation_repo.create(Conversation(title=question[:60]))
            conversation_id = str(conversation.id)

        history = await self.message_repo.by_conversation(conversation_id, limit=20)
        history_text = "\n".join(f"{m.role}: {m.content}" for m in history[-_MAX_HISTORY_TURNS:])

        await self.message_repo.create(
            ChatMessage(conversation_id=conversation_id, role=MessageRole.USER, content=question)
        )

        sql_generated: str | None = None
        sources: list[str] = []
        context_parts: list[str] = []

        try:
            sql_generated, rows = await self.sql_generator.generate_and_execute(
                question, history_text
            )
            if rows:
                preview = rows[:_MAX_SQL_ROWS_IN_CONTEXT]
                context_parts.append(f"SQL query results ({len(rows)} total rows):\n{preview}")
                sources.append(f"sql:{sql_generated[:80]}")
        except Exception as exc:  # noqa: BLE001 - SQL is a nice-to-have, not required
            logger.warning(
                "SQL generation/execution unavailable, using retrieval only", error=str(exc)
            )

        try:
            retrieved = await self.retrieval.hybrid_search(
                question, filters, limit=_MAX_RETRIEVED_RECORDS
            )
        except Exception as exc:  # noqa: BLE001 - never let retrieval failure kill the turn
            logger.warning("Retrieval unavailable", error=str(exc))
            retrieved = []

        for result in retrieved:
            snippet = result.content[:_MAX_CONTEXT_CHARS_PER_RECORD]
            context_parts.append(
                f"[{result.platform}/{result.source_type}/{result.source_id[:8]}] {snippet}"
            )
            sources.append(f"{result.source_type}:{result.source_id}")

        context_text = (
            "\n\n".join(context_parts) if context_parts else "(no relevant records found)"
        )

        messages: list[dict[str, str]] = [{"role": "system", "content": ASSISTANT_SYSTEM_PROMPT}]
        if history_text:
            messages.append(
                {
                    "role": "system",
                    "content": CONVERSATION_MEMORY_PROMPT.format(history=history_text),
                }
            )
        style_prompt = _style_prompt_for(question, context_text)
        if style_prompt:
            messages.append({"role": "system", "content": style_prompt})
        user_turn = f"Context:\n{context_text}\n\nQuestion: {question}"
        messages.append({"role": "user", "content": user_turn})

        try:
            response = await self.client.chat.completions.create(
                model=self.model, messages=messages, temperature=0.3  # type: ignore[arg-type]
            )
        except Exception as exc:
            raise AssistantError(f"Chat completion failed: {exc}") from exc

        answer = (response.choices[0].message.content or "").strip()
        usage = response.usage
        prompt_tokens = getattr(usage, "prompt_tokens", None)
        completion_tokens = getattr(usage, "completion_tokens", None)
        execution_time_ms = (time.perf_counter() - start) * 1000

        assistant_message = await self.message_repo.create(
            ChatMessage(
                conversation_id=conversation_id,
                role=MessageRole.ASSISTANT,
                content=answer,
                sources=sources,
                sql_generated=sql_generated,
                model_used=self.model,
                execution_time_ms=execution_time_ms,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
            )
        )

        await self.query_log_repo.create(
            QueryLog(
                conversation_id=conversation_id,
                query_text=question,
                retrieved_document_ids=[s for s in sources if not s.startswith("sql:")],
                filters_applied=_filters_to_json(filters),
                latency_ms=execution_time_ms,
            )
        )
        await self.assistant_log_repo.create(
            AssistantLog(
                conversation_id=conversation_id,
                message_id=str(assistant_message.id),
                prompt_used=user_turn[:4000],
                sql_generated=sql_generated,
                model_used=self.model,
                execution_time_ms=execution_time_ms,
                token_usage={
                    "prompt_tokens": prompt_tokens or 0,
                    "completion_tokens": completion_tokens or 0,
                },
            )
        )

        return assistant_message
