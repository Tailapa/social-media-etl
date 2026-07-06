"""Channel (YouTube-style) and Video models.

Modeled separately from Author/Post because "channel" carries subscriber
semantics distinct from a generic profile, and "video" carries duration /
transcript semantics distinct from a generic post — but both still funnel
into the same Author/Post tables via the normalization layer's mapping, so
retrieval and the AI assistant never need platform-specific branches.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import Field, computed_field

from app.models.pydantic.base import BaseSchema, IdentifiedMixin, SoftDeleteMixin, TimestampMixin
from app.models.pydantic.enums import PlatformName


class Channel(IdentifiedMixin, TimestampMixin, SoftDeleteMixin, BaseSchema):
    platform: PlatformName
    platform_channel_id: str
    author_id: str
    name: str
    description: str | None = None
    subscriber_count: int | None = Field(default=None, ge=0)
    video_count: int | None = Field(default=None, ge=0)
    total_views: int | None = Field(default=None, ge=0)
    country: str | None = None
    platform_metadata: dict = Field(default_factory=dict)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def dedup_key(self) -> str:
        return f"{self.platform}:{self.platform_channel_id}"


class Video(IdentifiedMixin, TimestampMixin, SoftDeleteMixin, BaseSchema):
    platform: PlatformName
    platform_video_id: str
    channel_id: str
    post_id: str | None = None
    title: str
    description: str | None = None
    transcript: str | None = None
    duration_seconds: float | None = Field(default=None, ge=0)
    thumbnail_url: str | None = None
    video_url: str | None = None
    published_at: datetime | None = None
    language: str | None = None
    platform_metadata: dict = Field(default_factory=dict)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def dedup_key(self) -> str:
        return f"{self.platform}:{self.platform_video_id}"

    @computed_field  # type: ignore[prop-decorator]
    @property
    def has_transcript(self) -> bool:
        return bool(self.transcript and self.transcript.strip())
