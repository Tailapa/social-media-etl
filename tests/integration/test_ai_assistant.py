"""Integration tests for app/ai/assistant.py's Assistant.ask() and
app/ai/sql_generator.py's SQLGenerator.

Every external dependency (OpenAI client, retrieval, SQL generation,
conversation/message/query-log/assistant-log persistence) is a constructor-
injected fake — no real network/API/DB call happens anywhere in this file.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest

from app.ai.assistant import Assistant
from app.ai.sql_generator import SQLGenerator
from app.models.pydantic import AssistantLog, ChatMessage, Conversation, QueryLog
from app.retrieval.models import RetrievalResult
from app.utils.exceptions import UnsafeSQLError

# --- Fake OpenAI client -------------------------------------------------------


def make_fake_openai_client(
    *, contents: list[str] | None = None, prompt_tokens: int = 10, completion_tokens: int = 5
) -> Any:
    """A fake shaped like `AsyncOpenAI`: `.chat.completions.create` is an
    `AsyncMock` returning canned responses (one per call, via `side_effect`,
    so successive `ask()` calls can be told apart in the conversation-memory
    test) shaped like the real `ChatCompletion` the assistant reads from.
    """
    contents = contents or ["a generated answer"]

    def _response_for(content: str) -> SimpleNamespace:
        message = SimpleNamespace(content=content)
        choice = SimpleNamespace(message=message)
        usage = SimpleNamespace(prompt_tokens=prompt_tokens, completion_tokens=completion_tokens)
        return SimpleNamespace(choices=[choice], usage=usage)

    responses = [_response_for(c) for c in contents]
    create = AsyncMock(side_effect=responses if len(responses) > 1 else None)
    if len(responses) == 1:
        create.return_value = responses[0]
    client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    return client


# --- Fakes for Assistant's other dependencies --------------------------------


class FakeRetrievalService:
    def __init__(
        self, results: list[RetrievalResult] | None = None, raise_exc: Exception | None = None
    ) -> None:
        self.results = results or []
        self.raise_exc = raise_exc
        self.calls: list[tuple[str, Any, int]] = []

    async def hybrid_search(self, question: str, filters: Any, limit: int = 8) -> list[Any]:
        self.calls.append((question, filters, limit))
        if self.raise_exc:
            raise self.raise_exc
        return self.results


class FakeSQLGenerator:
    def __init__(
        self,
        sql: str | None = "SELECT 1",
        rows: list[dict] | None = None,
        raise_exc: Exception | None = None,
    ) -> None:
        self.sql = sql
        self.rows = rows if rows is not None else [{"count": 1}]
        self.raise_exc = raise_exc
        self.calls: list[tuple[str, str]] = []

    async def generate_and_execute(
        self, question: str, conversation_context: str = ""
    ) -> tuple[str, list[dict]]:
        self.calls.append((question, conversation_context))
        if self.raise_exc:
            raise self.raise_exc
        return self.sql, self.rows


class FakeConversationRepo:
    def __init__(self) -> None:
        self.created: list[Conversation] = []

    async def create(self, conversation: Conversation) -> Conversation:
        self.created.append(conversation)
        return conversation


class FakeMessageRepo:
    def __init__(self) -> None:
        self.store: dict[str, list[ChatMessage]] = {}
        self.created: list[ChatMessage] = []

    async def create(self, message: ChatMessage) -> ChatMessage:
        self.created.append(message)
        self.store.setdefault(message.conversation_id, []).append(message)
        return message

    async def by_conversation(self, conversation_id: str, *, limit: int = 20) -> list[ChatMessage]:
        return list(self.store.get(conversation_id, []))[:limit]


class FakeQueryLogRepo:
    def __init__(self) -> None:
        self.created: list[QueryLog] = []

    async def create(self, log: QueryLog) -> QueryLog:
        self.created.append(log)
        return log


class FakeAssistantLogRepo:
    def __init__(self) -> None:
        self.created: list[AssistantLog] = []

    async def create(self, log: AssistantLog) -> AssistantLog:
        self.created.append(log)
        return log


def make_assistant(
    *,
    client: Any = None,
    retrieval: Any = None,
    sql_generator: Any = None,
    conversation_repo: Any = None,
    message_repo: Any = None,
    query_log_repo: Any = None,
    assistant_log_repo: Any = None,
) -> tuple[Assistant, dict[str, Any]]:
    fakes = {
        "client": client or make_fake_openai_client(),
        "retrieval": retrieval if retrieval is not None else FakeRetrievalService(),
        "sql_generator": sql_generator if sql_generator is not None else FakeSQLGenerator(),
        "conversation_repo": conversation_repo or FakeConversationRepo(),
        "message_repo": message_repo or FakeMessageRepo(),
        "query_log_repo": query_log_repo or FakeQueryLogRepo(),
        "assistant_log_repo": assistant_log_repo or FakeAssistantLogRepo(),
    }
    assistant = Assistant(
        retrieval=fakes["retrieval"],
        sql_generator=fakes["sql_generator"],
        client=fakes["client"],
        model="test-model",
        conversation_repo=fakes["conversation_repo"],
        message_repo=fakes["message_repo"],
        query_log_repo=fakes["query_log_repo"],
        assistant_log_repo=fakes["assistant_log_repo"],
    )
    return assistant, fakes


# --- Assistant.ask() happy path ----------------------------------------------


async def test_ask_happy_path_cites_sql_and_retrieval_sources():
    retrieval = FakeRetrievalService(
        results=[
            RetrievalResult(
                source_type="post",
                source_id="post-123",
                platform="instagram",
                content="hello world content",
                score=0.9,
            )
        ]
    )
    sql_generator = FakeSQLGenerator(sql="SELECT 1", rows=[{"count": 1}])
    assistant, fakes = make_assistant(retrieval=retrieval, sql_generator=sql_generator)

    message = await assistant.ask("What's trending?")

    assert any(s.startswith("sql:SELECT 1") for s in message.sources)
    assert "post:post-123" in message.sources
    assert message.sql_generated == "SELECT 1"
    assert message.content == "a generated answer"

    assert len(fakes["query_log_repo"].created) == 1
    assert len(fakes["assistant_log_repo"].created) == 1
    # user turn + assistant turn
    assert len(fakes["message_repo"].created) == 2
    assert fakes["conversation_repo"].created  # conversation_id was None -> created


async def test_ask_sql_generation_failure_falls_back_to_retrieval_only():
    sql_generator = FakeSQLGenerator(raise_exc=RuntimeError("sql backend down"))
    retrieval = FakeRetrievalService(
        results=[
            RetrievalResult(
                source_type="comment",
                source_id="c-1",
                platform="instagram",
                content="a comment",
                score=0.5,
            )
        ]
    )
    assistant, _fakes = make_assistant(sql_generator=sql_generator, retrieval=retrieval)

    message = await assistant.ask("Any SQL-requiring question?")

    assert message.sql_generated is None
    assert "comment:c-1" in message.sources


async def test_ask_retrieval_failure_does_not_crash():
    retrieval = FakeRetrievalService(raise_exc=RuntimeError("retrieval backend down"))
    sql_generator = FakeSQLGenerator(sql="SELECT 1", rows=[])  # no SQL sources either
    assistant, fakes = make_assistant(retrieval=retrieval, sql_generator=sql_generator)

    message = await assistant.ask("Question with no working retrieval")

    assert message.sources == []
    assert message.content == "a generated answer"
    assert len(fakes["message_repo"].created) == 2


async def test_ask_conversation_memory_included_in_second_prompt():
    client = make_fake_openai_client(contents=["first answer", "second answer"])
    assistant, fakes = make_assistant(client=client)

    await assistant.ask("What happened yesterday?", conversation_id="conv-1")
    await assistant.ask("And the day before that?", conversation_id="conv-1")

    assert client.chat.completions.create.await_count == 2
    second_call_kwargs = client.chat.completions.create.await_args_list[1].kwargs
    second_messages = second_call_kwargs["messages"]

    history_system_messages = [
        m["content"]
        for m in second_messages
        if m["role"] == "system" and "Previous conversation turns" in m["content"]
    ]
    assert history_system_messages, "expected a conversation-memory system message on turn 2"
    assert "What happened yesterday?" in history_system_messages[0]
    assert "first answer" in history_system_messages[0]

    # First call had no history yet.
    first_call_kwargs = client.chat.completions.create.await_args_list[0].kwargs
    first_messages = first_call_kwargs["messages"]
    assert not any(
        m["role"] == "system" and "Previous conversation turns" in m["content"]
        for m in first_messages
    )

    assert len(fakes["message_repo"].store["conv-1"]) == 4  # 2 user + 2 assistant turns


# --- SQLGenerator -------------------------------------------------------------


async def test_sql_generator_generate_strips_markdown_fences():
    client = make_fake_openai_client(contents=["```sql\nSELECT 1\n```"])
    generator = SQLGenerator(client=client, model="test-model")

    sql = await generator.generate("how many posts?")

    assert sql == "SELECT 1"


async def test_sql_generator_generate_and_execute_raises_on_unsafe_sql():
    client = make_fake_openai_client(contents=["DROP TABLE posts"])
    generator = SQLGenerator(client=client, model="test-model")

    with pytest.raises(UnsafeSQLError):
        await generator.generate_and_execute("delete everything")
