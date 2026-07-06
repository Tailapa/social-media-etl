"""Engagement metrics, normalized across platforms.

Not every platform exposes every metric (e.g. Instagram rarely exposes
shares); missing values are `None` rather than `0` so "unknown" is never
confused with "zero".
"""

from __future__ import annotations

from pydantic import Field, computed_field

from app.models.pydantic.base import BaseSchema, IdentifiedMixin, TimestampMixin


class Engagement(IdentifiedMixin, TimestampMixin, BaseSchema):
    post_id: str | None = None
    likes: int | None = Field(default=None, ge=0)
    views: int | None = Field(default=None, ge=0)
    shares: int | None = Field(default=None, ge=0)
    comments_count: int | None = Field(default=None, ge=0)
    saves: int | None = Field(default=None, ge=0)
    reactions: dict[str, int] = Field(default_factory=dict)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def total_engagement(self) -> int:
        """Sum of every known engagement signal; used for ranking/sorting."""
        return sum(
            v for v in (self.likes, self.shares, self.comments_count, self.saves) if v is not None
        ) + sum(self.reactions.values())

    @computed_field  # type: ignore[prop-decorator]
    @property
    def engagement_rate(self) -> float | None:
        """total_engagement / views, when views are known — a common
        cross-platform comparability metric requested by the AI assistant.
        """
        if not self.views:
            return None
        return round(self.total_engagement / self.views, 6)
