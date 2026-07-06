"""Scraper registry: the single seam through which `app.services` and
`app.ingestion` reach a platform's scraper, keyed by `PlatformName`.

Adding a new platform (Reddit, LinkedIn, TikTok, ...) means writing a
`BaseScraper` subclass in a new `app/apify/<platform>/scraper.py`,
decorating its class with `@register_scraper(PlatformName.X)`, and importing
that module below — nothing else in the codebase changes (see success
criteria #21, "Extensibility").
"""

from __future__ import annotations

from collections.abc import Callable

from app.apify.base import ApifyActorRunner, BaseScraper, ScrapeResult, get_apify_client
from app.models.pydantic.enums import PlatformName
from app.utils.exceptions import UnsupportedPlatformError

_REGISTRY: dict[PlatformName, type[BaseScraper]] = {}


def register_scraper(platform: PlatformName) -> Callable[[type[BaseScraper]], type[BaseScraper]]:
    """Class decorator: `@register_scraper(PlatformName.INSTAGRAM)`."""

    def _decorator(cls: type[BaseScraper]) -> type[BaseScraper]:
        _REGISTRY[platform] = cls
        return cls

    return _decorator


def get_scraper(platform: PlatformName | str) -> BaseScraper:
    """Instantiate the registered scraper for `platform`."""
    key = PlatformName(platform)
    if key not in _REGISTRY:
        raise UnsupportedPlatformError(f"No scraper registered for platform {key!r}")
    return _REGISTRY[key]()


def registered_platforms() -> list[PlatformName]:
    return list(_REGISTRY.keys())


# Import each platform package so its scraper module runs `@register_scraper`
# at import time. Done at the bottom of this module (after `register_scraper`
# is defined) to avoid a circular import.
from app.apify import instagram, twitter, youtube  # noqa: E402, F401

__all__ = [
    "ApifyActorRunner",
    "BaseScraper",
    "ScrapeResult",
    "get_apify_client",
    "register_scraper",
    "get_scraper",
    "registered_platforms",
]
