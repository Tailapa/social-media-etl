"""Unified Post model — the central content entity every platform maps into."""

from __future__ import annotations

from datetime import datetime

from pydantic import Field, computed_field, field_validator

from app.models.pydantic.base import BaseSchema, IdentifiedMixin, SoftDeleteMixin, TimestampMixin
from app.models.pydantic.enums import ContentType, PlatformName
from app.models.pydantic.media import Media


class Post(IdentifiedMixin, TimestampMixin, SoftDeleteMixin, BaseSchema):
    platform: PlatformName
    platform_post_id: str = Field(..., description="Native post ID on the platform")
    author_id: str
    content_type: ContentType
    caption: str | None = None
    content: str | None = None
    language: str | None = None
    url: str | None = None
    hashtags: list[str] = Field(default_factory=list)
    mentions: list[str] = Field(default_factory=list)
    urls: list[str] = Field(default_factory=list)
    # Not a `posts` column -- media lives in its own table, linked by
    # `post_id`. Carried here only so scrapers/normalizers can hand a
    # post's media along in one object; `exclude=True` keeps it out of the
    # payload `BaseRepository` sends to the `posts` table (see
    # `app/ingestion/pipeline.py::_ingest_media`, which reads this field
    # directly off the in-memory `Post`, not via a DB round-trip).
    media: list[Media] = Field(default_factory=list, exclude=True)
    posted_at: datetime | None = None
    is_pinned: bool = False
    is_sponsored: bool = False
    location: str | None = None
    platform_metadata: dict = Field(default_factory=dict)

    @field_validator("hashtags", "mentions", mode="before")
    @classmethod
    def _lower_list(cls, value: list[str] | None) -> list[str]:
        if not value:
            return []
        return [v.lstrip("#@").lower() for v in value]

    @computed_field  # type: ignore[prop-decorator]
    @property
    def dedup_key(self) -> str:
        """Stable key used to detect duplicate posts across ingestion runs."""
        return f"{self.platform}:{self.platform_post_id}"

    @computed_field  # type: ignore[prop-decorator]
    @property
    def has_media(self) -> bool:
        return len(self.media) > 0
