"""Hashtag and Mention models, plus the join-table representation for
`post_hashtags` (kept as a lightweight model rather than a bare tuple so the
repository layer has a typed object to insert)."""

from __future__ import annotations

from pydantic import field_validator

from app.models.pydantic.base import BaseSchema, CreatedAtMixin, IdentifiedMixin


class Hashtag(IdentifiedMixin, CreatedAtMixin, BaseSchema):
    tag: str

    @field_validator("tag")
    @classmethod
    def _normalize_tag(cls, value: str) -> str:
        return value.lstrip("#").strip().lower()


class Mention(IdentifiedMixin, CreatedAtMixin, BaseSchema):
    post_id: str | None = None
    comment_id: str | None = None
    username: str

    @field_validator("username")
    @classmethod
    def _normalize_username(cls, value: str) -> str:
        return value.lstrip("@").strip().lower()


class PostHashtag(BaseSchema):
    """Join-table row linking a Post to a Hashtag."""

    post_id: str
    hashtag_id: str
