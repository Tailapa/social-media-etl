"""Unified author/channel-owner model.

Every platform exposes a "who posted this" concept under a different name
(Instagram user, X user, YouTube channel owner). `Author` is the single
normalized representation so downstream code never branches on platform.
"""

from __future__ import annotations

from pydantic import Field, computed_field, field_validator

from app.models.pydantic.base import BaseSchema, IdentifiedMixin, SoftDeleteMixin, TimestampMixin
from app.models.pydantic.enums import PlatformName


class Author(IdentifiedMixin, TimestampMixin, SoftDeleteMixin, BaseSchema):
    platform: PlatformName
    platform_user_id: str = Field(..., description="Author's native ID on the platform")
    username: str
    display_name: str | None = None
    bio: str | None = None
    profile_url: str | None = None
    avatar_url: str | None = None
    is_verified: bool = False
    is_private: bool = False
    follower_count: int | None = Field(default=None, ge=0)
    following_count: int | None = Field(default=None, ge=0)
    post_count: int | None = Field(default=None, ge=0)
    location: str | None = None
    external_url: str | None = None
    platform_metadata: dict = Field(default_factory=dict)

    @field_validator("username")
    @classmethod
    def _normalize_username(cls, value: str) -> str:
        return value.lstrip("@").strip()

    @computed_field  # type: ignore[prop-decorator]
    @property
    def dedup_key(self) -> str:
        """Stable key used to merge duplicate author records on ingestion."""
        return f"{self.platform}:{self.platform_user_id}"
