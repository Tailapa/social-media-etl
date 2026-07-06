"""Value objects shared by every retrieval mode (keyword/semantic/hybrid)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass(slots=True)
class RetrievalFilters:
    """Metadata filters combinable with any search mode.

    All fields are optional and AND-combined — the assistant's SQL/retrieval
    planner fills in only the ones a user's question implies (e.g. "this
    month" -> date_from/date_to, "on Instagram" -> platform).
    """

    platform: str | None = None
    author_username: str | None = None
    hashtag: str | None = None
    date_from: datetime | None = None
    date_to: datetime | None = None
    min_likes: int | None = None
    content_types: list[str] | None = None


@dataclass(slots=True)
class RetrievalResult:
    """One ranked hit, uniform across keyword/semantic/hybrid/popularity
    search so the AI assistant never branches on which mode produced it.
    """

    source_type: str
    source_id: str
    platform: str
    content: str
    score: float
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def key(self) -> tuple[str, str]:
        return (self.source_type, self.source_id)
