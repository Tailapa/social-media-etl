"""Supabase client factory.

A single cached `Client` instance is shared by every repository. Kept in its
own module (rather than inline in each repository) so tests can monkeypatch
`get_supabase_client` once and have every repository pick up the fake.
"""

from __future__ import annotations

from functools import lru_cache

from supabase import Client, create_client

from app.config import get_settings
from app.logging import get_logger
from app.utils.exceptions import DatabaseConnectionError

logger = get_logger(__name__)


@lru_cache
def get_supabase_client() -> Client:
    """Return a process-wide cached Supabase client.

    Raises `DatabaseConnectionError` early (rather than letting the first
    query fail cryptically) if credentials are missing.
    """
    settings = get_settings()
    if not settings.has_supabase_credentials:
        raise DatabaseConnectionError(
            "Supabase credentials are not configured (SUPABASE_URL / SUPABASE_KEY)."
        )
    logger.info("Initializing Supabase client", url=settings.supabase_url)
    return create_client(settings.supabase_url, settings.supabase_key.get_secret_value())


def reset_client_cache() -> None:
    """Clear the cached client — used by tests between fixtures."""
    get_supabase_client.cache_clear()
