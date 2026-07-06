"""Common base classes / mixins reused by every domain model."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from pydantic import BaseModel, ConfigDict, Field


class BaseSchema(BaseModel):
    """Base for all domain (non-DB) Pydantic models.

    `populate_by_name` lets us accept both the Pythonic field name and any
    `alias` (used when a platform's raw JSON key differs), and
    `str_strip_whitespace` keeps scraped text clean without every normalizer
    remembering to call `.strip()`.
    """

    model_config = ConfigDict(
        populate_by_name=True,
        str_strip_whitespace=True,
        extra="ignore",
        use_enum_values=True,
    )


class IdentifiedMixin(BaseModel):
    """Adds a stable UUID primary key, generated client-side so records can
    be referenced before they are persisted (e.g. for embedding linkage).
    """

    id: uuid.UUID = Field(default_factory=uuid.uuid4)


class CreatedAtMixin(BaseModel):
    """Just `created_at`, for the append-only/log-style tables that have no
    `updated_at` column (hashtags, mentions, media, documents, messages,
    query_logs, assistant_logs — see each migration file). Using
    `TimestampMixin` on one of these would serialize an `updated_at` field
    PostgREST rejects with "column not found in schema cache".
    """

    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class TimestampMixin(CreatedAtMixin):
    """created_at / updated_at pair, for tables that have both columns and
    an `updated_at` trigger (see migrations/0002-0003).
    """

    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class SoftDeleteMixin(BaseModel):
    """Soft-delete flag mirrored on every DB table (see migrations/0001)."""

    deleted_at: datetime | None = None

    @property
    def is_deleted(self) -> bool:
        return self.deleted_at is not None
