"""SQLAlchemy engine used exclusively for executing AI-generated,
read-only SQL against the Supabase Postgres database.

This is deliberately separate from the Supabase client (which repositories
use for normal CRUD via PostgREST): the AI assistant needs to run arbitrary
SELECT queries the repositories don't have methods for, and SQLAlchemy gives
us a straightforward `text()` execution path plus statement-level safety
checks that would be awkward to bolt onto PostgREST.
"""

from __future__ import annotations

import re
from functools import lru_cache
from typing import Any

from sqlalchemy import Engine, create_engine, text

from app.config import get_settings
from app.logging import get_logger
from app.models.db.orm import KNOWN_TABLES
from app.utils.exceptions import DatabaseConnectionError, UnsafeSQLError

logger = get_logger(__name__)

# Only these statement-starting keywords are ever allowed through
# `execute_readonly_sql`. This is the last line of defense against a
# prompt-injected or hallucinated mutating statement reaching the database.
_ALLOWED_SQL_PREFIXES = ("select", "with")
_FORBIDDEN_KEYWORDS = (
    "insert",
    "update",
    "delete",
    "drop",
    "alter",
    "truncate",
    "grant",
    "revoke",
    "create",
    "--",
    ";--",
)


@lru_cache
def get_engine() -> Engine:
    settings = get_settings()
    if not settings.supabase_db_url:
        raise DatabaseConnectionError("SUPABASE_DB_URL is not configured.")
    return create_engine(settings.supabase_db_url, pool_pre_ping=True, pool_size=5)


_TABLE_REF_RE = re.compile(r"\b(?:from|join)\s+([a-zA-Z_][a-zA-Z0-9_]*)", re.IGNORECASE)
# CTE aliases (`WITH recent AS (...)`, `, older AS (...)`) are legitimate
# "table-like" references that aren't in `KNOWN_TABLES` — they're collected
# here so `validate_sql_tables` doesn't reject a query for using its own
# `WITH` clause, which `SQL_GENERATION_PROMPT` explicitly encourages.
_CTE_ALIAS_RE = re.compile(r"(?:with|,)\s+([a-zA-Z_][a-zA-Z0-9_]*)\s+as\s*\(", re.IGNORECASE)
# Forbidden keywords must match as whole words: a naive substring check would
# reject any query touching `created_at`/`updated_at`/`deleted_at` columns
# (which every table in this schema has) because they contain "create",
# "update", and "delete" as substrings.
_FORBIDDEN_KEYWORD_RE = re.compile(
    r"\b(" + "|".join(re.escape(k) for k in _FORBIDDEN_KEYWORDS) + r")\b", re.IGNORECASE
)


def assert_sql_is_safe(sql: str) -> None:
    """Guard against anything but a single read-only SELECT/CTE statement."""
    normalized = sql.strip().lower()
    if not normalized.startswith(_ALLOWED_SQL_PREFIXES):
        raise UnsafeSQLError(f"Only SELECT/WITH statements are permitted, got: {sql[:80]!r}")
    if ";" in normalized.rstrip(";"):
        raise UnsafeSQLError("Multiple statements are not permitted.")
    match = _FORBIDDEN_KEYWORD_RE.search(normalized)
    if match:
        raise UnsafeSQLError(f"Disallowed keyword {match.group(1)!r} found in generated SQL.")
    validate_sql_tables(sql)


def validate_sql_tables(sql: str) -> None:
    """Reject SQL that references tables outside the known schema.

    A cheap regex scan (not a full SQL parser) that catches the common
    failure mode of a hallucinated table name; anything more exotic still
    gets caught by Postgres itself when the query runs. CTE aliases defined
    by the query's own `WITH` clause are allowed even though they aren't
    real tables.
    """
    referenced = {m.group(1).lower() for m in _TABLE_REF_RE.finditer(sql)}
    cte_aliases = {m.group(1).lower() for m in _CTE_ALIAS_RE.finditer(sql)}
    unknown = referenced - KNOWN_TABLES - cte_aliases
    if unknown:
        raise UnsafeSQLError(f"Generated SQL references unknown table(s): {sorted(unknown)}")


def execute_readonly_sql(sql: str, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """Validate and execute a single read-only SQL statement, returning rows
    as a list of dicts.
    """
    assert_sql_is_safe(sql)
    engine = get_engine()
    logger.info("Executing AI-generated SQL", sql=sql)
    with engine.connect() as conn:
        result = conn.execute(text(sql), params or {})
        rows: list[dict[str, Any]] = [dict(row._mapping) for row in result]
    return rows
