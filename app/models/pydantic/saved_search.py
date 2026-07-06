"""Saved Search model: a persisted filter preset, natural-language scraping
prompt, or frequently asked AI question a user wants to re-run later.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import Field

from app.models.pydantic.base import BaseSchema, IdentifiedMixin, SoftDeleteMixin, TimestampMixin


class SavedSearchKind(StrEnum):
    FILTER = "filter"
    SCRAPE_PROMPT = "scrape_prompt"
    AI_QUESTION = "ai_question"


class SavedSearch(IdentifiedMixin, TimestampMixin, SoftDeleteMixin, BaseSchema):
    name: str
    kind: SavedSearchKind
    payload: dict = Field(default_factory=dict)
