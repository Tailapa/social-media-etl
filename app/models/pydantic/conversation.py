"""Chat/assistant persistence models: Conversation, ChatMessage, QueryLog.

These back the "store every user query and AI response" requirement — every
assistant turn is captured with enough metadata (SQL used, sources cited,
latency, token usage) to reconstruct and audit the interaction later.
"""

from __future__ import annotations

from pydantic import Field, computed_field

from app.models.pydantic.base import (
    BaseSchema,
    CreatedAtMixin,
    IdentifiedMixin,
    SoftDeleteMixin,
    TimestampMixin,
)
from app.models.pydantic.enums import MessageRole


class Conversation(IdentifiedMixin, TimestampMixin, SoftDeleteMixin, BaseSchema):
    user_id: str | None = None
    title: str | None = None
    is_archived: bool = False

    @computed_field  # type: ignore[prop-decorator]
    @property
    def display_title(self) -> str:
        return self.title or "New conversation"


class ChatMessage(IdentifiedMixin, CreatedAtMixin, BaseSchema):
    conversation_id: str
    role: MessageRole
    content: str
    sources: list[str] = Field(default_factory=list, description="Cited record IDs / URLs")
    sql_generated: str | None = None
    model_used: str | None = None
    execution_time_ms: float | None = Field(default=None, ge=0)
    prompt_tokens: int | None = Field(default=None, ge=0)
    completion_tokens: int | None = Field(default=None, ge=0)


class QueryLog(IdentifiedMixin, CreatedAtMixin, BaseSchema):
    """Raw log of every user query, independent of the chat message it
    produced — kept separate so query analytics survive even if the chat
    UI/message schema changes.
    """

    conversation_id: str | None = None
    query_text: str
    retrieved_document_ids: list[str] = Field(default_factory=list)
    filters_applied: dict = Field(default_factory=dict)
    latency_ms: float | None = Field(default=None, ge=0)


class AssistantLog(IdentifiedMixin, CreatedAtMixin, BaseSchema):
    """Log of the assistant's generation step: prompt, model, SQL, timing."""

    conversation_id: str | None = None
    message_id: str | None = None
    prompt_used: str
    sql_generated: str | None = None
    model_used: str
    execution_time_ms: float | None = Field(default=None, ge=0)
    token_usage: dict[str, int] = Field(default_factory=dict)
    error: str | None = None
