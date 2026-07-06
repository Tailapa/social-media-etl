"""Media attachment model (images/videos/audio attached to a post/comment)."""

from __future__ import annotations

from pydantic import Field, field_validator

from app.models.pydantic.base import BaseSchema, CreatedAtMixin, IdentifiedMixin
from app.models.pydantic.enums import MediaType


class Media(IdentifiedMixin, CreatedAtMixin, BaseSchema):
    post_id: str | None = None
    media_type: MediaType
    url: str
    thumbnail_url: str | None = None
    width: int | None = Field(default=None, ge=0)
    height: int | None = Field(default=None, ge=0)
    duration_seconds: float | None = Field(default=None, ge=0)
    file_size_bytes: int | None = Field(default=None, ge=0)
    alt_text: str | None = None
    order_index: int = 0

    @field_validator("url")
    @classmethod
    def _must_be_http(cls, value: str) -> str:
        if not value.startswith(("http://", "https://")):
            raise ValueError(f"Media url must be http(s): {value!r}")
        return value
