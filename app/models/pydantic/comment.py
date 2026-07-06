"""Comment / Reply / Thread models.

A `Reply` is modeled as a `Comment` with `parent_comment_id` set, rather than
a separate class hierarchy — platforms treat replies as nested comments, and
duplicating the schema would violate DRY for no benefit. `Thread` groups a
root comment with its replies for the retrieval/assistant layer to consume
as a single conversational unit.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import Field, computed_field, field_validator

from app.models.pydantic.base import BaseSchema, IdentifiedMixin, SoftDeleteMixin, TimestampMixin
from app.models.pydantic.enums import PlatformName


class Comment(IdentifiedMixin, TimestampMixin, SoftDeleteMixin, BaseSchema):
    platform: PlatformName
    platform_comment_id: str
    post_id: str
    author_id: str
    parent_comment_id: str | None = None
    content: str
    language: str | None = None
    likes: int | None = Field(default=None, ge=0)
    reply_count: int | None = Field(default=None, ge=0)
    hashtags: list[str] = Field(default_factory=list)
    mentions: list[str] = Field(default_factory=list)
    posted_at: datetime | None = None
    platform_metadata: dict = Field(default_factory=dict)

    @field_validator("content")
    @classmethod
    def _non_empty(cls, value: str) -> str:
        if not value or not value.strip():
            raise ValueError("Comment content cannot be empty")
        return value

    @computed_field  # type: ignore[prop-decorator]
    @property
    def dedup_key(self) -> str:
        return f"{self.platform}:{self.platform_comment_id}"

    @computed_field  # type: ignore[prop-decorator]
    @property
    def is_reply(self) -> bool:
        return self.parent_comment_id is not None


class Reply(Comment):
    """Semantic alias for a Comment whose parent_comment_id is required."""

    parent_comment_id: str


class Thread(BaseSchema):
    """A root comment plus its nested replies, materialized for retrieval."""

    root: Comment
    replies: list[Comment] = Field(default_factory=list)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def total_participants(self) -> int:
        return len({c.author_id for c in [self.root, *self.replies]})
