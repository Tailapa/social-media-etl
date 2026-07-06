# Infrastructure & Utility Layer — line-by-line reference

This document covers the foundational modules that almost every other part of the
app depends on: configuration, database access, logging, and generic utilities
(exceptions, retry, text helpers). For each file we explain every
import/class/function/field and *why* it's written that way, then trace real call
sites elsewhere in `app/` (grepped, not guessed).

---

## `app/config/__init__.py`

```python
from app.config.settings import Settings, get_settings

__all__ = ["Settings", "get_settings"]
```

A pure re-export shim. Its only job is so the rest of the codebase can write
`from app.config import get_settings` instead of reaching into the submodule
path `app.config.settings`. This is a common pattern in this codebase (see also
`app/database/__init__.py` and `app/logging/__init__.py`) — the package
`__init__.py` defines the *public* surface, the submodule holds the
implementation. `__all__` documents that surface for `from app.config import *`
and for static analysis tools.

**Where used:** every module that needs settings imports from the package, not
the submodule — e.g. `app/database/supabase_client.py:14`, `app/database/sql_engine.py:19`,
`app/logging/logger.py:21`, `app/apify/base/client.py`, `app/embeddings/providers.py:15`,
`app/ai/assistant.py`, `app/ai/sql_generator.py`, `app/services/scrape_service.py`. One
file (`app/config/settings.py` itself) is the only place `Settings`/`get_settings`
are *defined*, and this `__init__.py` is the only place they're re-exported.

---

## `app/config/settings.py`

### Purpose (module docstring)

> "All environment-driven configuration lives here as a single Pydantic Settings
> object so every other module has one place to source secrets and tunables from,
> instead of reading `os.environ` ad-hoc."

This is the single source of truth for every environment variable / `.env` value
the app reads. Centralizing it means: one place to see what config exists, one
place to add validation, and no module anywhere else calls `os.environ.get(...)`
directly.

### Imports

```python
from __future__ import annotations
```
PEP 563 — postpones evaluation of annotations, letting the file use newer-style
union syntax (`str | Path`) and forward references without runtime cost.

```python
from functools import lru_cache
```
Used to memoize `get_settings()` — see below.

```python
from pathlib import Path
from typing import Literal
```
`Path` types the `log_dir` setting so it's a real filesystem path object (not a
raw string) by the time any caller uses it (e.g. `settings.log_dir.mkdir(...)`
in `app/logging/logger.py:56`). `Literal` restricts `app_env` to an exact,
closed set of valid strings — catches a typo'd environment name (e.g.
`"prod"` instead of `"production"`) at settings-construction time rather than
silently comparing false everywhere later.

```python
from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
```
- `BaseSettings` (from `pydantic-settings`, not plain `pydantic`) — a
  `BaseModel` subclass that automatically reads field values from environment
  variables and/or a `.env` file, matching by field name (case-insensitively
  here). This is *why* `Settings()` can be constructed with zero arguments and
  still be populated — the values come from the environment, not from
  positional/keyword args.
- `SettingsConfigDict` — typed configuration for how that env-loading behaves
  (see `model_config` below).
- `SecretStr` — a wrapper type that prevents accidental credential leakage:
  its `repr()`/`str()` render as `**********` instead of the real value, so a
  stray `print(settings)` or an exception message that includes the settings
  object won't leak `apify_api_token`/`supabase_key`/`openai_api_key` into logs
  or tracebacks. Getting the actual value requires the explicit
  `.get_secret_value()` call, which every consumer in this codebase uses
  deliberately at the point of use (see call sites below) — a visible, greppable
  "yes, I intend to use the real secret here" marker.
- `Field(...)` — used here just to supply `default=SecretStr("")` for the three
  secret fields (a bare `SecretStr("")` default would otherwise be evaluated
  once at class-definition time and shared — using `Field(default=...)` is the
  idiomatic Pydantic way to declare it, though for an immutable value like this
  it's mostly stylistic consistency).
- `field_validator` — Pydantic v2 decorator for per-field validation/coercion
  (used for `log_dir` below).

### `class Settings(BaseSettings)`

```python
model_config = SettingsConfigDict(
    env_file=".env",
    env_file_encoding="utf-8",
    extra="ignore",
    case_sensitive=False,
)
```
- `env_file=".env"` — in addition to real OS environment variables,
  `pydantic-settings` will read a `.env` file in the working directory. This is
  what lets local development set `SUPABASE_URL=...` in a checked-in-ignored
  `.env` file instead of exporting shell variables.
- `extra="ignore"` — unknown keys in the environment/`.env` (e.g. a stray var
  unrelated to this app) are silently dropped rather than raising a validation
  error. This matters because a `.env` file is often shared/co-located with
  other tools' variables.
- `case_sensitive=False` — `SUPABASE_URL`, `supabase_url`, and `Supabase_Url`
  all bind to the same field. Environment variables are conventionally
  upper-case; the Python attribute is lower-case; this setting removes the need
  to fight that mismatch.

### Fields, grouped by comment header

| Field | Type | Default | Why |
|---|---|---|---|
| `app_env` | `Literal["development","staging","production","test"]` | `"development"` | Drives `is_production` and (per `docs/environment_variables.md`) informs which deploy-specific behavior is active. |
| `log_level` | `str` | `"INFO"` | Read by `app/logging/logger.py` to set the level on both the console and file sinks. |
| `log_dir` | `Path` | `Path("logs")` | Directory Loguru writes `app.jsonl`/`errors.jsonl` into; coerced from string via the validator below since env vars arrive as plain strings. |
| `apify_api_token` | `SecretStr` | `SecretStr("")` | Apify platform auth token — consumed by `app/apify/base/client.py:36` (`ApifyClient(settings.apify_api_token.get_secret_value())`). |
| `apify_instagram_profile_actor` / `_post_actor` / `_hashtag_actor` / `_comment_actor` | `str` | actor slugs, e.g. `"apify/instagram-profile-scraper"` | Which published Apify Actor ID to run for each Instagram scrape type. Kept as settings (not hard-coded in the scraper) so a different/forked actor can be swapped in via env var without a code change. Used throughout `app/apify/instagram/scraper.py` (lines 40, 55, 87, 132). |
| `apify_twitter_scraper_actor` | `str` | `"apidojo/tweet-scraper"` | Same idea for Twitter; used at `app/apify/twitter/scraper.py:56,67,100,133,157`. |
| `apify_youtube_scraper_actor` / `_comment_actor` / `_transcript_actor` | `str` | e.g. `"streamers/youtube-scraper"` | Same idea for YouTube; used at `app/apify/youtube/scraper.py:70,94,130,170`. |
| `supabase_url` | `str` | `""` | REST endpoint for the Supabase project; consumed by `get_supabase_client()`. |
| `supabase_key` | `SecretStr` | `SecretStr("")` | Supabase API key (anon or service role); same consumer. |
| `supabase_db_url` | `str` | `""` | Raw Postgres connection string (different from `supabase_url`, which is the PostgREST HTTP endpoint) — used exclusively by `app/database/sql_engine.py:get_engine` for direct SQLAlchemy access, because PostgREST has no generic "run arbitrary read-only SQL" endpoint. |
| `openai_api_key` | `SecretStr` | `SecretStr("")` | Used by both the embedding provider (`app/embeddings/providers.py:40`) and the two OpenAI-backed AI assistant/SQL-generator classes (`app/ai/assistant.py:98`, `app/ai/sql_generator.py:30`). |
| `openai_chat_model` | `str` | `"gpt-4o-mini"` | Default chat-completion model name; overridable per-instance but sourced from here as the fallback (`app/ai/assistant.py:99`, `app/ai/sql_generator.py:31`). |
| `openai_embedding_model` | `str` | `"text-embedding-3-small"` | Default embedding model name (`app/embeddings/providers.py:38`). |
| `embedding_dimensions` | `int` | `1536` | Expected vector length for the default embedding model — used as the fallback in `OpenAIEmbeddingProvider.__init__` (`app/embeddings/providers.py:39`) and matches the dimension the `pgvector` column is sized to; must stay in sync with whatever `openai_embedding_model` actually returns. |
| `max_concurrent_scrapes` | `int` | `5` | Caps how many scrape tasks run concurrently. Consumed by `ScrapeService.__init__` (`app/services/scrape_service.py:38`) to size an `asyncio.Semaphore` in `scrape_many` (line 90), preventing an unbounded burst of simultaneous Apify actor runs when a caller passes a large batch of targets. |

```python
@field_validator("log_dir", mode="before")
@classmethod
def _coerce_path(cls, value: str | Path) -> Path:
    return Path(value)
```
`mode="before"` runs *before* Pydantic's own type coercion/validation, on the
raw input. Since env vars and `.env` values always arrive as plain strings,
without this validator Pydantic would still coerce `str -> Path` automatically
in most cases — but declaring it explicitly makes the string→Path conversion
visible and is defensive against being passed an already-a-`Path` value too
(the `isinstance` union `str | Path` covers both), which is idempotent (`Path(Path(x)) == Path(x)`).

```python
@property
def is_production(self) -> bool:
    return self.app_env == "production"
```
A convenience predicate so callers write `settings.is_production` instead of
repeating the string comparison. **Notable finding:** grepping `app/` for
`is_production` finds no call site in application code — it's referenced only
by `tests/unit/test_config_and_database.py:48-52` and documented in
`docs/environment_variables.md:10`. It's defined and tested but not yet
consumed anywhere in the live code path — likely reserved for future
environment-gated behavior (e.g. disabling debug endpoints in prod).

```python
@property
def has_apify_credentials(self) -> bool:
    return bool(self.apify_api_token.get_secret_value())

@property
def has_supabase_credentials(self) -> bool:
    return bool(self.supabase_url and self.supabase_key.get_secret_value())

@property
def has_openai_credentials(self) -> bool:
    return bool(self.openai_api_key.get_secret_value())
```
Three "is this credential group actually configured" checks, each returning
`bool` by wrapping the secret's real value (via `.get_secret_value()`) in
`bool(...)` — an empty string is falsy, so an unset credential correctly
reports `False` without ever needing to compare against `""` explicitly (which
would fail for `SecretStr` since it doesn't support direct equality-to-string
without unwrapping).

Real consumer: `app/database/supabase_client.py:29` — `get_supabase_client()`
uses `has_supabase_credentials` to fail fast with a clear
`DatabaseConnectionError` instead of letting `create_client()` fail later with
a cryptic network/auth error. `has_apify_credentials` and
`has_openai_credentials` are, like `is_production`, currently exercised only by
`tests/unit/test_config_and_database.py` (lines 60-95) and documented in
`docs/environment_variables.md:38-39` as the *intended* pattern for other
modules to adopt, but no other application module currently calls them
directly (e.g. `app/apify/base/client.py` and `app/embeddings/providers.py`
construct their respective clients unconditionally rather than checking these
flags first).

```python
@lru_cache
def get_settings() -> Settings:
    """Return a cached, process-wide Settings instance."""
    return Settings()
```
`@lru_cache` with no arguments means this behaves like a lazy singleton: the
*first* call constructs a real `Settings()` (which reads env vars / `.env` at
that moment), and every subsequent call anywhere in the process returns the
exact same cached instance — so `Settings()` is not re-parsed from the
environment on every call, and every module in the app shares one consistent
view of configuration for the process's lifetime. Unlike
`get_supabase_client()`/`get_engine()` (below), there's no
`reset_settings_cache()` helper — tests instead monkeypatch environment
variables and construct `Settings()` directly rather than clearing this cache
(see `tests/unit/test_config_and_database.py`).

**Where `get_settings()` is called** (representative, not exhaustive — it's
used in essentially every module that needs configuration):
`app/apify/youtube/scraper.py`, `app/apify/twitter/scraper.py`,
`app/apify/instagram/scraper.py`, `app/apify/base/client.py`,
`app/services/scrape_service.py:38`, `app/ai/assistant.py`,
`app/ai/sql_generator.py`, `app/embeddings/providers.py`,
`app/database/sql_engine.py:47`, `app/database/supabase_client.py:28`,
`app/logging/logger.py:45`.

---

## `app/database/__init__.py`

```python
from app.database.schema_metadata import SCHEMA_DESCRIPTION
from app.database.sql_engine import assert_sql_is_safe, execute_readonly_sql, get_engine
from app.database.supabase_client import get_supabase_client, reset_client_cache

__all__ = [
    "SCHEMA_DESCRIPTION",
    "assert_sql_is_safe",
    "execute_readonly_sql",
    "get_engine",
    "get_supabase_client",
    "reset_client_cache",
]
```
Same "public facade" pattern as `app/config/__init__.py`: it aggregates the
three database submodules (Supabase client, raw SQL engine, schema metadata)
into one importable namespace, `app.database`. Note `validate_sql_tables` (also
defined in `sql_engine.py`) is deliberately **not** re-exported here — it's an
internal helper only called by `assert_sql_is_safe` itself, not something
outside callers should invoke directly.

**Where used:** `app/ai/sql_generator.py:13` — `from app.database import
SCHEMA_DESCRIPTION, assert_sql_is_safe, execute_readonly_sql` — is the one
place all three of those are consumed together, which makes sense: that's the
module that builds the AI's NL→SQL prompt and runs whatever it generates.
`app/repositories/*` and `app/retrieval/service.py` import `get_supabase_client`
from the package root too.

---

## `app/database/supabase_client.py`

### Purpose (module docstring)
> "A single cached `Client` instance is shared by every repository. Kept in its
> own module ... so tests can monkeypatch `get_supabase_client` once and have
> every repository pick up the fake."

This is the one and only place a `supabase.Client` object is constructed. Every
repository (`app/repositories/base.py`, `embedding_repository.py`, etc.) and the
retrieval service go through this function instead of each doing their own
`create_client(...)` — that's both DRY and, per the docstring, a deliberate
testability seam.

### Imports
```python
from functools import lru_cache
from supabase import Client, create_client
from app.config import get_settings
from app.logging import get_logger
from app.utils.exceptions import DatabaseConnectionError
```
- `Client`/`create_client` — the official `supabase-py` SDK type and factory.
- `get_settings` — to read `supabase_url`/`supabase_key`.
- `get_logger` — structured logging, module-bound (`logger = get_logger(__name__)`
  at module scope, so every log line from this module is tagged
  `component=app.database.supabase_client`).
- `DatabaseConnectionError` — the specific exception raised when credentials
  are missing, so a caller could catch just this and not, say, a
  `RecordNotFoundError`.

### `get_supabase_client() -> Client`
```python
@lru_cache
def get_supabase_client() -> Client:
    settings = get_settings()
    if not settings.has_supabase_credentials:
        raise DatabaseConnectionError(
            "Supabase credentials are not configured (SUPABASE_URL / SUPABASE_KEY)."
        )
    logger.info("Initializing Supabase client", url=settings.supabase_url)
    return create_client(settings.supabase_url, settings.supabase_key.get_secret_value())
```
- `@lru_cache` (no-arg function) — process-wide singleton, exactly like
  `get_settings()`. One `Client` object (with its own connection
  pooling/session under the hood) is created once and reused everywhere,
  instead of every repository call spinning up a new HTTP client.
- The credential check + explicit raise is the "fail fast, fail clearly"
  design mentioned in the docstring: without it, a misconfigured deployment
  would only discover the problem when the *first query* runs, deep inside
  some repository method, with a much less obvious underlying HTTP/auth error.
- `logger.info(..., url=settings.supabase_url)` — structured log call (Loguru
  keyword-argument binding, see the logging section) that records which
  Supabase project URL was connected to, without ever logging the secret key
  (only `.get_secret_value()` is passed to `create_client`, never logged).
- Return type `Client` — the raw untyped-beyond-SDK client; callers use its
  `.table(name)`/`.rpc(name)` query-builder API directly (see
  `app/repositories/base.py:45`: `get_supabase_client().table(self.table_name)`).

**Call sites (representative — this is used by nearly every repository/service
that touches the DB):**
- `app/repositories/base.py:45` — `BaseRepository`'s generic query builder.
- `app/repositories/embedding_repository.py:111` — direct use inside the
  embedding repository (likely for the `match_embeddings` RPC call, which
  doesn't go through the generic base-repository table helper).
- `app/retrieval/service.py:68` — keyword/full-text search path.

### `reset_client_cache() -> None`
```python
def reset_client_cache() -> None:
    """Clear the cached client — used by tests between fixtures."""
    get_supabase_client.cache_clear()
```
Calls `functools.lru_cache`'s built-in `.cache_clear()` on the decorated
function. This exists purely for test isolation: without it, once any test
calls `get_supabase_client()` (e.g. against a fake `SUPABASE_URL`), every
subsequent test in the same process would keep getting that same cached
(possibly now-wrong) client, because `lru_cache` has no TTL/invalidation of its
own. **Notable finding:** despite the docstring claiming it's "used by tests
between fixtures," grepping `tests/` for `reset_client_cache` finds zero call
sites — it's exported (`app/database/__init__.py`) and defined, but not
currently invoked by the test suite. Tests instead appear to monkeypatch
`get_supabase_client` at the repository level rather than exercising the real
cache-clearing path.

---

## `app/database/schema_metadata.py`

### Purpose (module docstring)
> "Human-readable schema description used to ground the AI assistant's SQL
> generation prompts ... Kept as a static Python structure rather than
> introspected live from the database so prompt construction never depends on
> a live DB connection and stays fast/deterministic."

This module defines exactly one thing: the constant `SCHEMA_DESCRIPTION`, a
triple-quoted string documenting every table, its columns, and its foreign-key
relationships in the Postgres schema. It's a prompt-engineering artifact, not a
functional/runtime schema — its only "consumer" is an LLM prompt.

```python
SCHEMA_DESCRIPTION = """
platforms(id, name, display_name, is_active, created_at, updated_at)
authors(id, platform, platform_user_id, username, ...)
...
""".strip()
```
Design choices worth calling out:
- **Static, hand-maintained text, not live introspection.** The docstring
  explains why: introspecting `information_schema` at runtime would make every
  AI-assistant request depend on an extra DB round trip and would be
  non-deterministic if the schema changes mid-session; a static string is
  instant and stable, at the cost of needing to be manually kept in sync with
  `migrations/`.
- **Compact per-table notation** (`table(col1, col2, fk -> other.col, ...)`) —
  dense enough to fit many tables in a single prompt without wasting tokens on
  verbose formatting, while still conveying FK relationships (`author_id ->
  authors.id`) that a plain column list wouldn't.
- **The trailing "Notes:" section is arguably the most important part.** It
  encodes *institutional knowledge that isn't derivable from column names
  alone* — e.g.:
  - which tables have a `deleted_at` (soft-delete) column and which don't,
    explicitly listing both sides to prevent the LLM from hallucinating a
    `deleted_at` filter on a table (like `embeddings` or `messages`) that would
    produce a "column does not exist" SQL error.
  - `platform` columns being denormalized plain text matching `platforms.name`
    (not a FK to `platforms.id`, a `uuid`) — this pre-empts a specific
    type-mismatch bug (`text = uuid`) an LLM would otherwise plausibly generate
    by assuming a normalized foreign-key join.
  - The hard constraint that only `SELECT`/`WITH` are ever valid — restated
    here (in the prompt) in addition to being *enforced* in code by
    `assert_sql_is_safe` (defense in depth: guide the model away from
    mutating SQL, then hard-block it if it still tries).
- `.strip()` at the end — trims the leading/trailing newline from the
  triple-quoted literal so the string embedded in the prompt doesn't start
  with a blank line.

**Where used:** exactly one consumer, `app/ai/sql_generator.py:36` —
`schema=SCHEMA_DESCRIPTION` is interpolated into the NL→SQL prompt template
(`SQL_GENERATION_PROMPT`) so the LLM has the real table/column shape (and the
gotchas above) available when it writes a `SELECT` for a natural-language
question. Also re-exported via `app/database/__init__.py:1,6`.

---

## `app/database/sql_engine.py`

### Purpose (module docstring)
> "SQLAlchemy engine used exclusively for executing AI-generated, read-only SQL
> ... This is deliberately separate from the Supabase client ... the AI
> assistant needs to run arbitrary SELECT queries the repositories don't have
> methods for, and SQLAlchemy gives us a straightforward `text()` execution
> path plus statement-level safety checks that would be awkward to bolt onto
> PostgREST."

This is the **only** place in the app that runs arbitrary, LLM-generated SQL —
and consequently the module carries the most safety-critical logic in the
database layer: it must guarantee that whatever string an LLM hallucinates
can never mutate or exfiltrate data outside a plain `SELECT`.

### Imports
```python
import re
from functools import lru_cache
from typing import Any
from sqlalchemy import Engine, create_engine, text
from app.config import get_settings
from app.logging import get_logger
from app.models.db.orm import KNOWN_TABLES
from app.utils.exceptions import DatabaseConnectionError, UnsafeSQLError
```
- `sqlalchemy.text()` — wraps a raw SQL string as an executable, parameterized
  statement (`conn.execute(text(sql), params)`), giving safe bind-parameter
  substitution without string-formatting SQL by hand.
- `KNOWN_TABLES` from `app/models/db/orm.py:302` —
  `frozenset[str] = frozenset(metadata.tables.keys())`, i.e. the live set of
  every table name declared in the SQLAlchemy ORM metadata (which mirrors
  `migrations/`). This is the whitelist used to catch hallucinated table names
  (see `validate_sql_tables` below); `app/models/db/orm.py:10-11` explicitly
  documents that every migrated table must be registered there *because*
  `assert_sql_is_safe` depends on it.
- `UnsafeSQLError` / `DatabaseConnectionError` — both from
  `app/utils/exceptions.py`; see the exceptions section for their place in the
  hierarchy.

### Module-level constants

```python
_ALLOWED_SQL_PREFIXES = ("select", "with")
_FORBIDDEN_KEYWORDS = (
    "insert", "update", "delete", "drop", "alter", "truncate",
    "grant", "revoke", "create", "--", ";--",
)
```
The allow-list (`_ALLOWED_SQL_PREFIXES`) is checked first and is the primary
gate: only statements that *start with* `select` or `with` (a CTE) pass. The
deny-list (`_FORBIDDEN_KEYWORDS`) is a defense-in-depth backstop for mutating
keywords appearing *anywhere* in the statement (e.g. inside a subquery or a
`WITH` block), plus SQL-comment markers (`--`, `;--`) that could otherwise be
used to smuggle a second statement or comment out the rest of a query in an
injection attempt.

```python
_TABLE_REF_RE = re.compile(r"\b(?:from|join)\s+([a-zA-Z_][a-zA-Z0-9_]*)", re.IGNORECASE)
_CTE_ALIAS_RE = re.compile(r"(?:with|,)\s+([a-zA-Z_][a-zA-Z0-9_]*)\s+as\s*\(", re.IGNORECASE)
_FORBIDDEN_KEYWORD_RE = re.compile(
    r"\b(" + "|".join(re.escape(k) for k in _FORBIDDEN_KEYWORDS) + r")\b", re.IGNORECASE
)
```
- `_TABLE_REF_RE` — finds every identifier following `FROM`/`JOIN`, i.e. every
  table the query actually reads from.
- `_CTE_ALIAS_RE` — finds every alias defined by the query's *own* `WITH ... AS
  (` clauses. This exists specifically so a legitimate CTE alias (e.g. `WITH
  recent AS (...) SELECT * FROM recent`) isn't rejected as an "unknown table" —
  the code comment at line 54-57 explains this was added because
  `SQL_GENERATION_PROMPT` *encourages* the LLM to use CTEs.
- `_FORBIDDEN_KEYWORD_RE` — built dynamically from `_FORBIDDEN_KEYWORDS` with
  `\b` word boundaries. The comment at lines 59-62 explains a subtle bug this
  prevents: a naive substring check for `"create"`/`"update"`/`"delete"` would
  false-positive on every table's `created_at`/`updated_at`/`deleted_at`
  *column names*, which appear in nearly every legitimate query in this schema.
  Word-boundary regex avoids that false rejection while still catching the
  actual keywords as standalone tokens.

### `get_engine() -> Engine`
```python
@lru_cache
def get_engine() -> Engine:
    settings = get_settings()
    if not settings.supabase_db_url:
        raise DatabaseConnectionError("SUPABASE_DB_URL is not configured.")
    return create_engine(settings.supabase_db_url, pool_pre_ping=True, pool_size=5)
```
Same singleton-via-`lru_cache` pattern as `get_settings`/`get_supabase_client`.
- `pool_pre_ping=True` — SQLAlchemy issues a cheap liveness check before
  handing out a pooled connection, so a connection that's gone stale (e.g. the
  DB restarted, or a cloud Postgres idled it out) is transparently
  reconnected instead of surfacing as a confusing "connection closed" error on
  the next query.
- `pool_size=5` — small, fixed connection pool; reasonable for a background
  AI-query workload that isn't meant to be high-throughput.
- Same fail-fast pattern as `get_supabase_client`: missing config raises
  immediately rather than deferring to a cryptic connection error at query
  time.

### `assert_sql_is_safe(sql: str) -> None`
```python
def assert_sql_is_safe(sql: str) -> None:
    normalized = sql.strip().lower()
    if not normalized.startswith(_ALLOWED_SQL_PREFIXES):
        raise UnsafeSQLError(f"Only SELECT/WITH statements are permitted, got: {sql[:80]!r}")
    if ";" in normalized.rstrip(";"):
        raise UnsafeSQLError("Multiple statements are not permitted.")
    match = _FORBIDDEN_KEYWORD_RE.search(normalized)
    if match:
        raise UnsafeSQLError(f"Disallowed keyword {match.group(1)!r} found in generated SQL.")
    validate_sql_tables(sql)
```
The full gate, run in order:
1. **Prefix check** — must start with `select`/`with`. `sql[:80]!r` in the
   error message truncates the (potentially huge, LLM-hallucinated) statement
   to a safe preview length and `!r` shows it as a repr (visible quoting,
   escapes control characters) for a clean log/error line.
2. **Single-statement check** — after stripping *one* trailing `;`, if any `;`
   remains, the string contains multiple statements (a classic SQL-injection
   stacking pattern), so it's rejected outright.
3. **Forbidden-keyword scan** — catches mutating verbs anywhere in the
   statement, e.g. inside a subquery.
4. **Table whitelist check** — delegates to `validate_sql_tables`.

This function is the single choke-point that both real consumers
(`execute_readonly_sql` below, and `app/ai/sql_generator.py`) call before ever
letting a generated string reach a live connection — called from two
independent places precisely so the AI SQL generator can validate *and reject
before even trying to run* a bad query (giving it a chance to retry/regenerate;
see `app/ai/sql_generator.py:62,66` catching `UnsafeSQLError` specifically),
while `execute_readonly_sql` re-validates as a non-bypassable last line of
defense regardless of caller.

### `validate_sql_tables(sql: str) -> None`
```python
def validate_sql_tables(sql: str) -> None:
    referenced = {m.group(1).lower() for m in _TABLE_REF_RE.finditer(sql)}
    cte_aliases = {m.group(1).lower() for m in _CTE_ALIAS_RE.finditer(sql)}
    unknown = referenced - KNOWN_TABLES - cte_aliases
    if unknown:
        raise UnsafeSQLError(f"Generated SQL references unknown table(s): {sorted(unknown)}")
```
Explicitly documented as "a cheap regex scan (not a full SQL parser)" —
it doesn't try to be a complete SQL grammar; it just extracts everything that
*looks like* a table reference (`FROM x`, `JOIN y`) and rejects any that isn't
in `KNOWN_TABLES` and isn't one of the query's own CTE aliases. Real syntax
errors or more exotic SQL are explicitly left for Postgres itself to reject at
execution time — this function's only job is to catch the common case of a
hallucinated table name *before* spending a DB round-trip on it.

### `execute_readonly_sql(sql, params=None) -> list[dict[str, Any]]`
```python
def execute_readonly_sql(sql: str, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    assert_sql_is_safe(sql)
    engine = get_engine()
    logger.info("Executing AI-generated SQL", sql=sql)
    with engine.connect() as conn:
        result = conn.execute(text(sql), params or {})
        rows: list[dict[str, Any]] = [dict(row._mapping) for row in result]
    return rows
```
The actual execution path: re-validates safety (belt-and-suspenders even
though callers are expected to have already called `assert_sql_is_safe`),
grabs the shared engine, logs the exact SQL being run (useful audit trail for
an AI-generated query), opens a connection via a context manager (guarantees
it's returned to the pool even on error), executes with `sqlalchemy.text()`
and optional bind `params`, and converts each SQLAlchemy `Row` to a plain
`dict` via its `._mapping` (column-name → value) so the caller gets
JSON-serializable plain dicts rather than SQLAlchemy row objects.

**Full call-site trace (exhaustive):**
- `app/database/__init__.py:2,7-9` — re-exports `assert_sql_is_safe`,
  `execute_readonly_sql`, `get_engine` (not `validate_sql_tables`, which stays
  internal).
- `app/ai/sql_generator.py:13,36,62,65` — the sole real consumer:
  1. imports `SCHEMA_DESCRIPTION, assert_sql_is_safe, execute_readonly_sql`
     from `app.database`.
  2. calls `assert_sql_is_safe(sql)` on the LLM's generated SQL (line 62) —
     if it raises `UnsafeSQLError`, the generator catches it specifically
     (line 66) to distinguish "the SQL itself was rejected" from other
     failure modes.
  3. on success, calls `execute_readonly_sql(sql)` (line 65) to actually run
     it and get rows back.
- `app/models/db/orm.py:11` — code comment noting `KNOWN_TABLES` (defined
  there) "is the whitelist `assert_sql_is_safe` uses" — documents the
  cross-module dependency in the other direction.

---

## `app/logging/__init__.py`

```python
from app.logging.logger import configure_logging, get_logger

__all__ = ["configure_logging", "get_logger"]
```
Same facade pattern again. Notably, `app.logging` shadows the Python standard
library's own `logging` module name — this is safe because it's a relative
package inside `app/`, so `import app.logging` and stdlib `import logging`
resolve to different modules, but it does mean nothing in this codebase should
ever write `import logging` expecting the stdlib module while also having
`app.logging` imported in a way that could be confused — the module docstring
in `logger.py` makes this explicit: "nothing else in the codebase should touch
`logging` or `loguru` directly."

**Where used:** every module that logs imports from here — `from app.logging
import get_logger` — rather than the submodule path.

---

## `app/logging/logger.py`

### Purpose (module docstring)
> "Structured logging setup built on Loguru + Rich. A single `configure_logging()`
> call wires up: a Rich-formatted console sink ... a rotating JSON file sink per
> app_env ... automatic masking of sensitive keys."

This module is the single integration point between the app and its logging
backend (Loguru, chosen over stdlib `logging` presumably for its simpler
API/structured "bind" semantics). No other module should call
`loguru.logger` or `logging.getLogger` directly — everything goes through
`get_logger(name)`.

### Imports
```python
from typing import TYPE_CHECKING, Any
from loguru import logger as _logger
from rich.console import Console
from rich.logging import RichHandler

from app.config import get_settings

if TYPE_CHECKING:
    from loguru import Record
```
- `_logger` (leading underscore) — the loguru module-level singleton logger,
  aliased privately so this module can wrap it without ever exposing the raw
  loguru object to callers (callers only ever get back
  `_logger.bind(component=name)`, a *bound* logger — see `get_logger` below).
- `rich.console.Console` / `RichHandler` — gives the console sink colorized,
  pretty-printed tracebacks and log formatting for local development
  readability.
- `Record` imported only under `TYPE_CHECKING` — it's Loguru's type for a log
  record dict, used purely as a type hint on `_mask_sensitive`; guarding the
  import behind `TYPE_CHECKING` avoids a real runtime import cost/dependency
  since it's only needed by static type checkers.

### Module-level state
```python
_SENSITIVE_KEYS = {"token", "api_key", "apikey", "password", "secret", "authorization", "key"}
_CONFIGURED = False
```
- `_SENSITIVE_KEYS` — substrings checked case-insensitively against any
  structured-logging key name; deliberately broad (`"key"` alone, not just
  `"api_key"`) to catch variants callers might use.
- `_CONFIGURED` — a module-level flag backing the idempotency of
  `configure_logging()` (see below) — a simple guard rather than, say, an
  `lru_cache`-wrapped no-return function, because `configure_logging` returns
  `None` and `lru_cache` on a side-effecting void function would be an unusual
  fit; an explicit flag is more legible here.

### `_mask_sensitive(record: Record) -> bool`
```python
def _mask_sensitive(record: Record) -> bool:
    extra = record.get("extra", {})
    for k in list(extra.keys()):
        if any(sensitive in k.lower() for sensitive in _SENSITIVE_KEYS):
            extra[k] = "***REDACTED***"
    return True
```
A Loguru **filter** function — filters in Loguru run before a sink emits a
record and return a `bool` deciding whether to emit it at all; this one always
returns `True` (never suppresses a log line) but uses the filter hook as a
convenient *mutation* point: it walks every keyword passed via structured
logging (`logger.info("msg", token="abc")` lands in `record["extra"]`) and
overwrites any whose *key name* matches a sensitive substring with a fixed
redaction marker, in place, before formatting/serialization happens. This is
the mechanism that keeps a stray `logger.info("...", api_key=settings.x...)`
call from ever writing a real secret into `app.jsonl`/`errors.jsonl` or the
console — masking happens centrally, once, rather than requiring every call
site to remember to redact.
`list(extra.keys())` — copies the keys before iterating because the loop
mutates `extra` (`extra[k] = ...`) during iteration; iterating directly over
`extra.keys()` while mutating the same dict would raise a `RuntimeError`.

### `configure_logging() -> None`
```python
def configure_logging() -> None:
    global _CONFIGURED
    if _CONFIGURED:
        return
    settings = get_settings()
    _logger.remove()
    ...
    _CONFIGURED = True
```
- `_logger.remove()` — Loguru ships with a default stderr sink pre-installed;
  this removes it so the app's three custom sinks (below) fully replace it —
  otherwise every log line would print twice (once via Loguru's default
  formatting, once via the custom ones).
- Idempotency guard (`if _CONFIGURED: return`) — since `get_logger()` (below)
  calls `configure_logging()` on *every* invocation, without this guard every
  single `get_logger(__name__)` call anywhere in the app (there are many, at
  import time, in nearly every module) would re-add the sinks, causing
  duplicated log output and repeatedly re-opening the log files.

Three sinks are configured:
1. **Console (Rich)**
   ```python
   console = Console(stderr=True)
   _logger.add(
       RichHandler(console=console, rich_tracebacks=True, markup=True),
       level=settings.log_level,
       format="{message}",
       filter=_mask_sensitive,
   )
   ```
   Writes to **stderr** (not stdout) — a deliberate convention that keeps logs
   separate from any actual program output on stdout (relevant since the
   Gradio UI/CLI might print real output). `rich_tracebacks=True` gives
   colorized, syntax-highlighted exception tracebacks. `format="{message}"` —
   minimal Loguru-side formatting because `RichHandler` does its own
   level/timestamp rendering; letting both format fully would double up.
2. **`app.jsonl`** — all levels at `settings.log_level`, `serialize=True` (each
   line is a JSON object — machine-parseable), `rotation="10 MB"` (new file
   once the current one hits 10 MB), `retention="14 days"` (older rotated
   files auto-deleted), `enqueue=True` (writes go through a background
   thread/queue — safe for use from async code / multiple threads without
   interleaved/corrupted lines).
3. **`errors.jsonl`** — same shape but hardcoded to `level="ERROR"` and a
   longer `retention="30 days"` — a separate, smaller, longer-retained stream
   of just the errors, so an on-call/debugging session doesn't have to grep
   the full firehose log for failures.

   Both file sinks live under `settings.log_dir` (`Path`), which is created
   with `settings.log_dir.mkdir(parents=True, exist_ok=True)` before the sinks
   are added — `parents=True` creates any missing parent directories,
   `exist_ok=True` avoids an error if the directory (or a previous run's logs)
   already exists.

   All three sinks pass `filter=_mask_sensitive` — the redaction applies
   uniformly regardless of destination.

### `get_logger(name: str) -> Any`
```python
def get_logger(name: str) -> Any:
    configure_logging()
    return _logger.bind(component=name)
```
- Calls `configure_logging()` every time (cheap no-op after the first call,
  per the guard above) — this means **any** module can call `get_logger(...)`
  at import time, in any order, without needing an explicit app-startup step
  to configure logging first; the first call anywhere in the process
  bootstraps it lazily.
- `_logger.bind(component=name)` — Loguru's `bind()` returns a new logger
  "view" that automatically attaches `component=name` as structured context to
  every message logged through it, without needing to pass it manually on
  every call. Callers universally do `logger = get_logger(__name__)` at
  module scope, so every log line is automatically tagged with which module
  emitted it.
- Return type `Any` — Loguru doesn't ship a distinct public type for a bound
  logger (it's still a `Logger` instance dynamically), so `Any` is the
  pragmatic, honest type hint rather than fighting Loguru's typing.

**Call sites:** used in nearly every module in the app (17+ files found via
grep) — representative examples: `app/database/supabase_client.py:18`,
`app/database/sql_engine.py:24`, `app/utils/retry.py:22`,
`app/apify/base/client.py:26`, `app/embeddings/providers.py:20`,
`app/embeddings/service.py`, `app/ingestion/pipeline.py`,
`app/retrieval/service.py`, `app/repositories/base.py`,
`app/gradio/chat_tab.py`, `app/gradio/analytics_tab.py`,
`app/ai/assistant.py`, `app/ai/sql_generator.py`,
`app/services/scrape_service.py`. All follow the same one-line convention:
`logger = get_logger(__name__)` at module scope, then `logger.info(...)`
/`logger.warning(...)`/`logger.error(...)` with structured keyword args.

---

## `app/utils/__init__.py`

This file is **completely empty (0 bytes)** — no re-exports at all, unlike
`app/config/__init__.py`, `app/database/__init__.py`, and
`app/logging/__init__.py`, which all follow a re-export facade pattern.
Practically, this means every consumer of `app/utils/*` must import from the
specific submodule directly — e.g. `from app.utils.exceptions import
EmbeddingError`, `from app.utils.retry import with_retry`, `from app.utils.text
import extract_hashtags` — there is no `from app.utils import EmbeddingError`
shortcut available. This is confirmed by every real import site found via grep
across `app/` and `tests/` (see each subsection below) — none of them import
from bare `app.utils`. Whether this is an intentional inconsistency (the
`utils` package genuinely has three independent, unrelated concerns —
exceptions, retry policy, text parsing — so a shared facade would be less
meaningful than for config/database/logging, each of which centers on one
thing) or simply an omission, the net effect today is the same: `app/utils/`
is a plain namespace package with no curated public surface.

---

## `app/utils/exceptions.py`

### Purpose (module docstring)
> "Every custom exception in the project derives from `AppError` so callers can
> catch broad or narrow failure classes as needed, and so the ingestion
> pipeline can distinguish recoverable errors (skip + log) from fatal ones
> (abort the run)."

This is the app's entire custom exception hierarchy — one file, one root class,
everything else a subclass. Centralizing it (rather than letting each module
define its own ad-hoc exception types) is what lets a caller several layers up
catch, e.g., `except RepositoryError` and know it covers every
persistence-layer failure regardless of which specific repository raised it.

### `AppError(Exception)` — the root
```python
class AppError(Exception):
    def __init__(self, message: str, *, context: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.context = context or {}
```
Every custom exception in the app is (transitively) an `AppError`, which means
`except AppError` at a top-level boundary (e.g. wrapping a whole ingestion run
or an AI assistant request) reliably catches *any* app-raised failure while
still letting genuinely unexpected exceptions (a real `KeyError`, `TypeError`
from a bug) propagate uncaught, since those are not `AppError` subclasses.
- `message: str` — stored both as `self.message` *and* passed to
  `super().__init__(message)` so `str(exc)` still works normally (standard
  `Exception` behavior) while also giving structured code access to the exact
  message without needing to re-parse `str(exc)`.
- `context: dict[str, Any] | None = None` (keyword-only, via the `*`) — an
  arbitrary structured payload attached to the exception for
  debugging/logging (e.g. `context={"payload": payload}` in
  `app/repositories/base.py:130`, `context={"count": len(texts)}` in
  `app/embeddings/providers.py:50`). Defaults to `{}` rather than `None` so
  every `AppError` instance can be treated uniformly as "has a context dict,"
  never requiring a `None`-check before use.

### The hierarchy, by section

**Scraping / Apify** — failures from the Apify integration layer:
- `ScraperError(AppError)` — base class for "something about running a scraper
  actor failed."
- `ApifyRunFailedError(ScraperError)` — "the Apify actor run itself finished
  in a FAILED/TIMED-OUT/ABORTED state." Raised in
  `app/apify/base/client.py:79,87,92` — three distinct raise sites inside
  `run_and_fetch`, covering (per the method's own docstring at line 62) the
  different ways a run can end up non-successful.
- `ApifyRateLimitError(ScraperError)` — "Apify returned an HTTP 429." Raised at
  `app/apify/base/client.py:76`, and — notably — this is the *one* exception
  type that `with_retry` is configured to catch and retry on in this codebase:
  `@with_retry(exceptions=(ApifyRateLimitError,), max_attempts=4, min_wait=2.0,
  max_wait=30.0)` decorates `run_and_fetch` itself (line 50), meaning a 429
  triggers up to 4 attempts with exponential backoff, while an
  `ApifyRunFailedError` (a genuine failure, not a transient rate limit) is
  *not* in the retry set and propagates immediately.
- `UnsupportedPlatformError(ScraperError)` — "no scraper is registered for the
  requested platform." Raised at `app/apify/__init__.py:36` — the scraper
  registry/factory's lookup-miss case (`f"No scraper registered for platform
  {key!r}"`).

**Validation / normalization** — for turning raw scraped payloads into the
app's Pydantic models:
- `ValidationFailedError(AppError)` — "raw payload failed Pydantic validation
  and could not be normalized."
- `NormalizationError(AppError)` — "a record could not be mapped into the
  unified schema."
  **Notable finding:** grepping the entire repository (`app/` and `tests/`)
  for both of these names finds **no `raise` site anywhere** — they are
  defined and covered by the parametrized hierarchy test
  (`tests/unit/test_utils.py:190-191`,
  `test_exception_hierarchy[NormalizationError-AppError]` /
  `[ValidationFailedError-AppError]`), but the normalization modules
  (`app/normalization/instagram.py`, `twitter.py`, `youtube.py`) do not
  currently raise either of them — they appear to be reserved/forward-looking
  additions to the hierarchy rather than exceptions currently thrown by live
  code. (Normalization failures observed via grep instead seem to propagate as
  whatever Pydantic itself raises, or aren't yet explicitly guarded.)

**Persistence** — database/repository failures:
- `RepositoryError(AppError)` — base class for repository/database failures;
  also used directly as a catch-all at `app/repositories/base.py:131,147,165,181`
  when a Supabase/PostgREST call fails for a reason that isn't a more specific
  known case (unique-constraint violation or not-found).
- `RecordNotFoundError(RepositoryError)` — "requested record does not exist."
  Raised at `app/repositories/base.py:92` (a `get`-by-id method) and again at
  line 183 inside what looks like an update path when the target row is
  missing.
- `DuplicateRecordError(RepositoryError)` — "insert violated a unique
  constraint (already ingested)." Raised at `app/repositories/base.py:130`,
  inside an `except` that presumably inspects the underlying PostgREST/Postgres
  error for a unique-violation signature before re-raising as this specific
  type — letting the ingestion pipeline treat "this record already exists" as
  an expected, skippable condition rather than a hard failure.
- `DatabaseConnectionError(RepositoryError)` — "could not reach
  Supabase/Postgres." Raised at `app/database/supabase_client.py:30` (missing
  credentials) and `app/database/sql_engine.py:49` (missing
  `SUPABASE_DB_URL`) — both are *configuration* failures caught before any
  network call is attempted, reusing this type because the failure mode ("we
  cannot reach the database") is the same from the caller's perspective
  whether it's because of bad config or an actual network outage.

**Embeddings / retrieval:**
- `EmbeddingError(AppError)` — "embedding generation failed." Raised at
  `app/embeddings/providers.py:49` inside `OpenAIEmbeddingProvider.embed_texts`,
  wrapping *any* exception (`except Exception as exc`) from the OpenAI SDK
  call into this app-specific type, with `context={"count": len(texts)}` so a
  caller/log can see how many texts were in the failed batch without parsing
  the message string. This raise site sits *inside* the same method that's
  decorated with `@with_retry(exceptions=(Exception,), max_attempts=3)` — the
  retry decorator wraps the whole method, so the OpenAI call is retried up to
  3 times before `EmbeddingError` is ever actually raised and allowed to
  propagate (since `with_retry(reraise=True)` lets the final failure through
  after retries exhaust).
- `RetrievalError(AppError)` — "hybrid retrieval query failed." Raised twice in
  `app/retrieval/service.py`: line 78 (`f"Keyword search failed: {exc}"`, the
  full-text-search path) and line 106 (`f"Semantic search failed: {exc}"`, the
  vector-search path) — both wrap arbitrary underlying failures from their
  respective query methods into one uniform type for callers of
  `RetrievalService`.

**AI assistant:**
- `AssistantError(AppError)` — base class for AI assistant failures. Raised
  directly at `app/ai/assistant.py:184` when a chat completion call fails
  (`f"Chat completion failed: {exc}"`).
- `SQLGenerationError(AssistantError)` — "LLM produced SQL that failed
  validation or execution." Raised at `app/ai/sql_generator.py:47` (the
  generation request itself failing) and line 69 (`f"Generated SQL execution
  failed: {exc}"`, when `execute_readonly_sql` throws something *other than*
  `UnsafeSQLError` — see next).
- `UnsafeSQLError(SQLGenerationError)` — "generated SQL contained a disallowed
  statement (e.g. DROP/DELETE)." This is the exception type raised throughout
  `app/database/sql_engine.py` (four raise sites: lines 72, 74, 77, 94 — one
  per safety-check failure mode in `assert_sql_is_safe`/`validate_sql_tables`).
  Its place in the hierarchy — a subclass of `SQLGenerationError`, itself a
  subclass of `AssistantError` — is deliberate: `app/ai/sql_generator.py:66`
  specifically `except UnsafeSQLError:` to handle "the SQL was rejected by our
  safety gate" as its own case (e.g. to retry generation with feedback),
  distinct from `except Exception` catching genuine execution errors (line
  68-69) that get wrapped into the *parent* `SQLGenerationError` instead. This
  is the one place in the codebase where the exception hierarchy's
  parent/child relationship is actively used to distinguish two different
  recovery strategies in code, not just for documentation.

### Summary table — who raises what

| Exception | Direct parent | Raised in |
|---|---|---|
| `AppError` | `Exception` | never raised directly — always via a subclass |
| `ScraperError` | `AppError` | never raised directly — always via a subclass |
| `ApifyRunFailedError` | `ScraperError` | `app/apify/base/client.py:79,87,92` |
| `ApifyRateLimitError` | `ScraperError` | `app/apify/base/client.py:76` (and retried by `with_retry`) |
| `UnsupportedPlatformError` | `ScraperError` | `app/apify/__init__.py:36` |
| `ValidationFailedError` | `AppError` | not currently raised anywhere in `app/` |
| `NormalizationError` | `AppError` | not currently raised anywhere in `app/` |
| `RepositoryError` | `AppError` | `app/repositories/base.py:131,147,165,181` |
| `RecordNotFoundError` | `RepositoryError` | `app/repositories/base.py:92,183` |
| `DuplicateRecordError` | `RepositoryError` | `app/repositories/base.py:130` |
| `DatabaseConnectionError` | `RepositoryError` | `app/database/supabase_client.py:30`, `app/database/sql_engine.py:49` |
| `EmbeddingError` | `AppError` | `app/embeddings/providers.py:49` |
| `RetrievalError` | `AppError` | `app/retrieval/service.py:78,106` |
| `AssistantError` | `AppError` | `app/ai/assistant.py:184` |
| `SQLGenerationError` | `AssistantError` | `app/ai/sql_generator.py:47,69` |
| `UnsafeSQLError` | `SQLGenerationError` | `app/database/sql_engine.py:72,74,77,94`; caught specifically at `app/ai/sql_generator.py:66` |

---

## `app/utils/retry.py`

### Purpose (module docstring)
> "Shared retry policies built on Tenacity. Centralizing retry configuration
> means every outbound call (Apify, Supabase, OpenAI) backs off and logs the
> same way, instead of each module reinventing its own loop."

A thin, opinionated wrapper around the third-party `tenacity` library, so
every retried call in the app (regardless of which external service it's
calling) shares one consistent backoff shape and one consistent log line on
each retry.

### Imports
```python
from collections.abc import Callable
from typing import Any, TypeVar
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential_jitter,
)
from app.logging import get_logger
```
- `retry` — Tenacity's core decorator factory; everything else here configures
  its behavior.
- `retry_if_exception_type(exceptions)` — a Tenacity retry-predicate that only
  retries when the raised exception is an instance of one of the given types
  (as opposed to retrying on *any* exception or on a return-value condition).
- `stop_after_attempt(max_attempts)` — caps total attempts (initial call +
  retries) rather than retrying forever.
- `wait_exponential_jitter(initial, max)` — exponential backoff with random
  jitter between attempts; jitter specifically avoids the "thundering herd"
  problem where many failed concurrent calls would otherwise all retry at
  exactly the same intervals.
- `T = TypeVar("T")` — used purely to make `with_retry`'s return type
  (`Callable[[Callable[..., T]], Callable[..., T]]`) generic, so a type
  checker knows the decorated function's signature/return type is preserved
  unchanged by the decorator.

### `_log_retry(retry_state: Any) -> None`
```python
def _log_retry(retry_state: Any) -> None:
    logger.warning(
        "Retrying after failure",
        attempt=retry_state.attempt_number,
        wait=str(retry_state.next_action.sleep) if retry_state.next_action else None,
        exception=str(retry_state.outcome.exception()) if retry_state.outcome else None,
    )
```
Passed to Tenacity's `before_sleep=` hook (see below), so it fires right
before each backoff sleep (not on the final, non-retried failure). Pulls
three pieces of context out of Tenacity's `RetryCallState` object: which
attempt number just failed, how long it's about to sleep before the next try,
and the string form of the exception that triggered the retry — all logged as
one structured `logger.warning(...)` call, giving an operator a clear trail of
"call X failed, retrying (attempt N, waiting Ys): <exception>" without every
retried function needing its own logging.

### `with_retry(...) -> Callable[[Callable[..., T]], Callable[..., T]]`
```python
def with_retry(
    *,
    exceptions: tuple[type[BaseException], ...] = (Exception,),
    max_attempts: int = 3,
    min_wait: float = 1.0,
    max_wait: float = 20.0,
) -> Callable[[Callable[..., T]], Callable[..., T]]:
    return retry(
        reraise=True,
        stop=stop_after_attempt(max_attempts),
        wait=wait_exponential_jitter(initial=min_wait, max=max_wait),
        retry=retry_if_exception_type(exceptions),
        before_sleep=_log_retry,
    )
```
- All parameters are keyword-only (`*`) — forces call sites to be
  self-documenting (`with_retry(max_attempts=4, min_wait=2.0)`, not a
  positional soup), important given there are four numeric-ish parameters that
  would otherwise be easy to transpose.
- `exceptions: tuple[type[BaseException], ...] = (Exception,)` — default
  retries on *any* `Exception`, but every real call site in this codebase
  narrows it explicitly (see below) — the broad default exists so a caller who
  doesn't care to be specific still gets sane behavior, while the two actual
  usages both deliberately scope it down.
- `reraise=True` — this is what makes `with_retry` behave transparently to
  callers: once all attempts are exhausted, Tenacity re-raises the *original*
  exception from the wrapped function, rather than wrapping it in its own
  `tenacity.RetryError`. Without this, every caller of a `with_retry`-decorated
  function would need to know to catch `RetryError` instead of the function's
  natural exception type — `reraise=True` means the decorator is invisible
  from the caller's perspective except for the delay and the extra log lines.
- Returns the configured `retry(...)` decorator itself (not yet applied to any
  function) — `with_retry(...)` is a decorator *factory*, used as
  `@with_retry(exceptions=(...), max_attempts=...)` directly above a function
  definition.

**Exhaustive call-site trace** (only two real usages in the whole codebase):
1. `app/apify/base/client.py:50` —
   ```python
   @with_retry(exceptions=(ApifyRateLimitError,), max_attempts=4, min_wait=2.0, max_wait=30.0)
   async def run_and_fetch(self, ...):
   ```
   Retries *only* on `ApifyRateLimitError` (HTTP 429) — a genuine actor
   failure (`ApifyRunFailedError`) is deliberately excluded from the retry
   set, since retrying a run that already failed for a substantive reason
   wouldn't help. 4 attempts, 2-30s backoff — tuned longer/more patient than
   the default, appropriate for a rate limit that may need real wall-clock
   time to clear.
2. `app/embeddings/providers.py:42` —
   ```python
   @with_retry(exceptions=(Exception,), max_attempts=3)
   async def embed_texts(self, texts: list[str]) -> list[list[float]]:
   ```
   Uses the broad `(Exception,)` default (explicitly passed rather than
   omitted) with the default `max_attempts=3` and default wait bounds (1-20s)
   — any transient OpenAI SDK failure (network blip, transient 500, etc.) is
   retried up to 3 times before the method's own `except Exception` re-wraps
   the final failure into `EmbeddingError` (see the exceptions section).

Both decorated functions are `async def` — Tenacity's `retry(...)` decorator
(from the sync `tenacity` API used here, not `tenacity.asyncio`) transparently
supports decorating async functions as of modern Tenacity versions, so no
special async-aware import was needed.

---

## `app/utils/text.py`

### Purpose (module docstring)
> "Text extraction helpers shared by every normalizer: hashtags, mentions,
> URLs, and a lightweight language guess. Kept dependency-free (stdlib regex)
> so normalization never blocks on an external NLP service."

Four small, pure, stdlib-only functions used by the per-platform normalization
modules (`app/normalization/instagram.py`, `twitter.py`, `youtube.py`) to pull
structured signals (hashtags, @mentions, URLs, a rough language guess) out of
raw caption/description/tweet text, without needing any network call or heavy
NLP dependency during ingestion.

### Module-level regexes
```python
_HASHTAG_RE = re.compile(r"(?<!\w)#(\w+)", re.UNICODE)
_MENTION_RE = re.compile(r"(?<!\w)@(\w+)", re.UNICODE)
_URL_RE = re.compile(r"https?://[^\s<>\"']+")
_ASCII_RE = re.compile(r"^[\x00-\x7F]*$")
```
- `_HASHTAG_RE` / `_MENTION_RE` — both use a negative lookbehind `(?<!\w)`
  before the `#`/`@` so `"e#mail"` or `"foo@bar"` (symbol preceded by a word
  character, i.e. embedded mid-word) isn't mistaken for a real hashtag/mention
  — a real hashtag/mention is only recognized at a word boundary.
  `re.UNICODE` (Python 3's `re` default for `str` patterns, but stated
  explicitly here) makes `\w` match Unicode word characters, not just ASCII,
  so a hashtag like `#café` is captured correctly.
- `_URL_RE` — matches `http(s)://` followed by any run of characters that
  aren't whitespace or one of `<>"'` — a pragmatic boundary that stops the
  match before it swallows trailing punctuation/markup that isn't actually
  part of the URL (further cleaned up in `extract_urls`, see below).
- `_ASCII_RE` — anchored full-string match (`^...$`) checking every character
  is in the ASCII range `\x00`-`\x7F`. Backing the crude language heuristic in
  `guess_language`.

### `extract_hashtags(text: str | None) -> list[str]`
```python
def extract_hashtags(text: str | None) -> list[str]:
    if not text:
        return []
    seen: dict[str, None] = {}
    for match in _HASHTAG_RE.findall(text):
        seen.setdefault(match.lower(), None)
    return list(seen.keys())
```
- Accepts `str | None` and short-circuits to `[]` for `None`/empty string —
  every caller in the normalization modules passes a possibly-missing field
  (e.g. a caption that might not exist), so this null-safety means callers
  never need their own guard before calling it.
- `seen: dict[str, None]` used as an **order-preserving deduplicating set** —
  Python dicts preserve insertion order (guaranteed since 3.7), and using a
  dict instead of a `set` here is what keeps the *first-seen* order stable in
  the output list, which a plain `set` would not guarantee. `.setdefault(x,
  None)` is a common idiom for "insert only if not already present," ignoring
  the value.
- `.lower()` — hashtags are case-normalized so `#Apify` and `#apify` count as
  the same tag.
- Returns `list(seen.keys())` — the plain list of unique, lowercased,
  order-preserved hashtags (without the leading `#`, since the capture group
  `(\w+)` excludes it).

### `extract_mentions(text: str | None) -> list[str]`
Structurally identical to `extract_hashtags` but for `@mentions` — same
null-safety, same order-preserving dedup-via-dict, same lowercase
normalization, same "no leading symbol in output" behavior.

### `extract_urls(text: str | None) -> list[str]`
```python
def extract_urls(text: str | None) -> list[str]:
    if not text:
        return []
    seen: dict[str, None] = {}
    for match in _URL_RE.findall(text):
        seen.setdefault(match.rstrip(").,"), None)
    return list(seen.keys())
```
Same null-safety/dedup/order-preservation shape as the other two, with one
extra step: `match.rstrip(").,")` strips trailing `)`, `.`, or `,` characters
from each match. This exists because URLs in free-form text are frequently
followed immediately by punctuation with no space (e.g. `"see
https://example.com."` or `"(https://example.com)"`) which `_URL_RE`'s greedy
character class would otherwise capture as part of the URL. Unlike the other
two functions, URLs are **not** lowercased (case matters in URL paths/query
strings, unlike hashtags/mentions).

### `guess_language(text: str | None) -> str`
```python
def guess_language(text: str | None) -> str:
    if not text or not text.strip():
        return "und"
    return "en" if _ASCII_RE.match(text) else "und"
```
Its own docstring is candid about the tradeoff: "Cheap heuristic: ASCII-only
text is assumed English, otherwise 'und' (undetermined). Real language
detection can be swapped in later without touching callers, since this is the
single seam they go through." So: blank/whitespace-only or `None` input → the
ISO 639-2-style `"und"` sentinel; otherwise, if every character is within the
ASCII range, it's *assumed* (not detected) to be English; any non-ASCII
character at all (accented Latin letters, CJK, Cyrillic, emoji, etc.) makes it
fall back to `"und"` rather than attempt real detection. This is intentionally
crude — good enough to populate a `language` column without a model
dependency, with the explicit design intent that a real detector could later
replace the function body without any caller needing to change, because this
function is the *only* place any caller goes through.

**Call-site trace:**
- `extract_hashtags`/`extract_mentions`/`extract_urls` — used identically in
  all three platform normalizers:
  - `app/normalization/twitter.py:14,98-100,131-132`
  - `app/normalization/youtube.py:18,105-107,160-161`
  - `app/normalization/instagram.py:16,107-109,137-138`

  In each normalizer, the pattern is: prefer any hashtags/mentions/URLs the
  raw Apify payload already provides (`raw.get("hashtags") or
  extract_hashtags(...)`), falling back to regex extraction only when the
  scraper didn't supply them (or, for a *second* record type in the same
  file — e.g. comments vs. posts — where the raw payload never has structured
  fields at all, so the fallback is unconditional, as seen at
  `twitter.py:131-132`, `youtube.py:160-161`, `instagram.py:137-138`).
- `guess_language` — **notable finding:** despite the module docstring
  claiming these helpers are "shared by every normalizer," grepping all of
  `app/` finds **no call site for `guess_language` anywhere in application
  code** — it's defined and thoroughly unit-tested
  (`tests/unit/test_utils.py:96-109`, covering ASCII, non-ASCII, `None`, and
  blank-string inputs) but none of `app/normalization/instagram.py`,
  `twitter.py`, or `youtube.py` currently call it to populate their
  `language` field. This mirrors the `is_production`/`has_apify_credentials`
  pattern seen in `Settings` — a fully implemented and tested piece of
  functionality that the rest of the app hasn't wired up yet.

---

## Cross-cutting observations

- **Three singleton-via-`lru_cache` factories** share the same shape:
  `get_settings()`, `get_supabase_client()`, `get_engine()`. All three:
  read configuration once, fail fast with a specific `AppError` subclass if
  required config is missing, and cache the result for the life of the
  process. Only `get_supabase_client` ships a public cache-reset helper
  (`reset_client_cache`), and even that is currently unused by the test suite.
- **Several pieces of implemented, tested functionality are not yet wired
  into any call path**: `Settings.is_production`, `Settings.has_apify_credentials`,
  `Settings.has_openai_credentials` (only `has_supabase_credentials` is
  actually consumed, by `get_supabase_client`), `app.utils.exceptions.ValidationFailedError`
  and `NormalizationError` (defined, never raised), and `app.utils.text.guess_language`
  (defined, never called by the normalizers whose docstring claims to share it).
  These read as forward-looking/defensive additions rather than dead code to
  delete — each is exercised by unit tests, just not yet by production code
  paths.
- **`app/utils/__init__.py` is the one package in this layer with no facade** —
  every other package (`config`, `database`, `logging`) re-exports its public
  API at the package root; `utils` does not, so its three submodules
  (`exceptions`, `retry`, `text`) are always imported by full submodule path
  throughout the codebase.
