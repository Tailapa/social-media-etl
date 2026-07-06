"""Application-wide exception hierarchy.

Every custom exception in the project derives from `AppError` so callers can
catch broad or narrow failure classes as needed, and so the ingestion
pipeline can distinguish recoverable errors (skip + log) from fatal ones
(abort the run).
"""

from __future__ import annotations

from typing import Any


class AppError(Exception):
    """Base class for all application-raised errors."""

    def __init__(self, message: str, *, context: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.context = context or {}


# --- Scraping / Apify ---
class ScraperError(AppError):
    """Raised when an Apify scraping operation fails."""


class ApifyRunFailedError(ScraperError):
    """The Apify actor run finished in a FAILED/TIMED-OUT/ABORTED state."""


class ApifyRateLimitError(ScraperError):
    """Apify returned an HTTP 429 rate-limit response."""


class UnsupportedPlatformError(ScraperError):
    """Requested platform has no registered scraper."""


# --- Validation / normalization ---
class ValidationFailedError(AppError):
    """Raw payload failed Pydantic validation and could not be normalized."""


class NormalizationError(AppError):
    """A record could not be mapped into the unified schema."""


# --- Persistence ---
class RepositoryError(AppError):
    """Base class for repository/database failures."""


class RecordNotFoundError(RepositoryError):
    """Requested record does not exist."""


class DuplicateRecordError(RepositoryError):
    """Insert violated a unique constraint (already ingested)."""


class DatabaseConnectionError(RepositoryError):
    """Could not reach Supabase / Postgres."""


# --- Embeddings / retrieval ---
class EmbeddingError(AppError):
    """Embedding generation failed."""


class RetrievalError(AppError):
    """Hybrid retrieval query failed."""


# --- AI assistant ---
class AssistantError(AppError):
    """Base class for AI assistant failures."""


class SQLGenerationError(AssistantError):
    """LLM produced SQL that failed validation or execution."""


class UnsafeSQLError(SQLGenerationError):
    """Generated SQL contained a disallowed statement (e.g. DROP/DELETE)."""
