"""Natural-language -> SQL generation, grounded in `SCHEMA_DESCRIPTION` and
validated/executed through `app.database.sql_engine` before any row reaches
the assistant.
"""

from __future__ import annotations

import re

from openai import AsyncOpenAI

from app.config import get_settings
from app.database import SCHEMA_DESCRIPTION, assert_sql_is_safe, execute_readonly_sql
from app.logging import get_logger
from app.prompts import SQL_GENERATION_PROMPT
from app.utils.exceptions import SQLGenerationError, UnsafeSQLError

logger = get_logger(__name__)

_FENCE_RE = re.compile(r"^```(?:sql)?\s*|\s*```$", re.IGNORECASE | re.MULTILINE)


def _strip_fences(text: str) -> str:
    return _FENCE_RE.sub("", text).strip().rstrip(";")


class SQLGenerator:
    def __init__(self, client: AsyncOpenAI | None = None, model: str | None = None) -> None:
        settings = get_settings()
        self.client = client or AsyncOpenAI(api_key=settings.openai_api_key.get_secret_value())
        self.model = model or settings.openai_chat_model

    async def generate(self, question: str, conversation_context: str = "") -> str:
        """Ask the LLM for a single read-only SQL statement answering `question`."""
        prompt = SQL_GENERATION_PROMPT.format(
            schema=SCHEMA_DESCRIPTION,
            conversation_context=conversation_context or "(none)",
            question=question,
        )
        try:
            response = await self.client.chat.completions.create(
                model=self.model,
                messages=[{"role": "user", "content": prompt}],
                temperature=0,
            )
        except Exception as exc:
            raise SQLGenerationError(f"SQL generation request failed: {exc}") from exc

        content = response.choices[0].message.content or ""
        return _strip_fences(content)

    async def generate_and_execute(
        self, question: str, conversation_context: str = ""
    ) -> tuple[str, list[dict]]:
        """Generate SQL, validate it's safe, and execute it.

        Raises `UnsafeSQLError` if the generated statement fails validation —
        callers (the assistant) should catch this and fall back to
        retrieval-only rather than surfacing it to the user directly.
        """
        sql = await self.generate(question, conversation_context)
        assert_sql_is_safe(sql)
        logger.info("Executing assistant-generated SQL", sql=sql, question=question)
        try:
            rows = execute_readonly_sql(sql)
        except UnsafeSQLError:
            raise
        except Exception as exc:
            raise SQLGenerationError(f"Generated SQL execution failed: {exc}") from exc
        return sql, rows
