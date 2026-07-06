"""Platform reference model — a row per supported source (instagram, x, ...)."""

from __future__ import annotations

from app.models.pydantic.base import BaseSchema, IdentifiedMixin, TimestampMixin
from app.models.pydantic.enums import PlatformName


class Platform(IdentifiedMixin, TimestampMixin, BaseSchema):
    name: PlatformName
    display_name: str
    is_active: bool = True
