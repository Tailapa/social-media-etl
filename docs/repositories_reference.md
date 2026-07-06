# `app/repositories/` — line-by-line reference

This document covers every file in `app/repositories/`, in the order they matter
most to understanding the layer: `base.py` first (the generic engine every other
repository is built on), then every concrete repository alphabetically as listed
in the assignment. Each section explains every import/class/field/function and
*why* it's written that way, then traces (via grep, not guesswork) every real
call site in `app/` and `tests/`.

`app/repositories/embedding_repository.py` is already documented in deep detail in
`docs/embedding_model_explained.md` (section 3b) — that write-up is the
authoritative source on `Document`/`EmbeddingRow`/pgvector parsing. Its section
here is intentionally more concise and cross-references that doc rather than
repeating it.

---

## `app/repositories/base.py`

```python
"""Generic repository base class. ..."""
from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any

from postgrest.exceptions import APIError
from pydantic import BaseModel

from app.database.supabase_client import get_supabase_client
from app.logging import get_logger
from app.utils.exceptions import DuplicateRecordError, RecordNotFoundError, RepositoryError

logger = get_logger(__name__)
```

### Imports and why

- `from __future__ import annotations` — PEP 563 postponed evaluation, needed
  because the class uses `ModelT` (a PEP 695 type parameter) and forward
  references like `ModelT | None` throughout method signatures.
- `asyncio` — every public method wraps the underlying **synchronous**
  `supabase-py`/`postgrest-py` client call in `asyncio.to_thread(...)`. The
  Supabase Python SDK has no native async client for the query builder used
  here, and the rest of the app (ingestion pipeline, retrieval service) is
  built around `asyncio.gather` over many concurrent scrape targets — a
  blocking network call made directly on the event loop would serialize all
  of that concurrency. Dispatching to a thread pool keeps the event loop free.
- `datetime`, `UTC` — used only by `soft_delete` to stamp `deleted_at` with the
  current UTC time as an ISO string (`postgrest` sends payloads as JSON, so a
  Python `datetime` must be pre-formatted to `.isoformat()`).
- `Any` — return type of the internal `_run()` closures (the raw postgrest
  `APIResponse`, whose exact type isn't worth importing/pinning here).
- `APIError` (from `postgrest.exceptions`) — the exception type raised by the
  underlying client on a failed HTTP-level DB operation (constraint
  violations, malformed queries, etc.). Caught explicitly so it can be
  translated into the app's own exception hierarchy (`RepositoryError` /
  `DuplicateRecordError`) rather than leaking a third-party exception type
  into calling code.
- `BaseModel` (pydantic) — only used as the upper bound of the generic type
  parameter `ModelT` (see class declaration below); every table-mapped model
  in the app is a `pydantic.BaseModel` subclass.
- `get_supabase_client` — factory for the single process-wide cached
  `supabase.Client` (see `app/database/supabase_client.py`); imported here so
  every repository can build its own `.table(...)` query without each
  concrete repository needing its own import.
- `get_logger` — structured logger; every write operation logs table name,
  conflict key, and row count for observability into what the ingestion
  pipeline actually persisted.
- `DuplicateRecordError`, `RecordNotFoundError`, `RepositoryError` — the
  app-wide exception hierarchy (`app/utils/exceptions.py`); `RepositoryError`
  is the generic "a DB op failed" wrapper, `DuplicateRecordError` and
  `RecordNotFoundError` are narrower subclasses used where the failure mode is
  distinguishable (unique-violation vs. update-target-missing) so calling
  code (e.g. the ingestion pipeline's per-item `try/except`) can react
  differently if it ever needs to.

### `class BaseRepository[ModelT: BaseModel]`

```python
class BaseRepository[ModelT: BaseModel]:
    table_name: str
    model: type[ModelT]
```

PEP 695 generic class syntax (Python 3.12+): `ModelT` is bound to `BaseModel`,
so every concrete subclass (`AuthorRepository(BaseRepository[Author])`, etc.)
gets fully-typed `create`/`get_by_id`/`list_all`/... return types (`Author`
instead of a bare `BaseModel`) for free, with zero per-method type annotation
duplication. `table_name`/`model` are **class attributes with no default** —
each subclass must set them (see every concrete repository below); they are
declared here purely for the type checker and for `__init__`'s runtime guard.

```python
    def __init__(self) -> None:
        if not getattr(self, "table_name", None) or not getattr(self, "model", None):
            raise NotImplementedError("Subclasses must set table_name and model")
```

Defensive check: if someone defines a subclass and forgets to set
`table_name`/`model` (e.g. copy-pastes a new repository and misses a line),
this fails loudly and immediately at construction time rather than with a
cryptic `AttributeError` deep inside `_table` on the first query.

```python
    @property
    def _table(self) -> Any:
        return get_supabase_client().table(self.table_name)
```

A fresh query builder for `self.table_name`, fetched on every access (not
cached) because `supabase-py`'s `.table(...)` object is a one-shot query
builder — chaining `.select().eq().limit()` mutates/returns builder state
meant to be consumed once by `.execute()`, so a cached instance would leak
filters across calls.

#### `_serialize` — the base payload builder

```python
    def _serialize(self, obj: ModelT) -> dict[str, Any]:
        payload = obj.model_dump(mode="json", exclude_none=False)
        for field_name in self.model.model_computed_fields:
            payload.pop(field_name, None)
        return payload
```

- `obj.model_dump(mode="json", exclude_none=False)` — `mode="json"` produces
  JSON-safe primitives (e.g. `uuid.UUID` → `str`, `datetime` → ISO string,
  enums → their plain value thanks to `use_enum_values=True` on
  `BaseSchema`) so the dict can be handed directly to `postgrest`'s
  `insert`/`upsert`, which serializes it as-is. `exclude_none=False` is
  explicit and deliberate: PostgREST needs an explicit `null` to *clear* a
  nullable column on update (e.g. wiping `parent_comment_id` back to `NULL`);
  omitting `None` fields would leave stale values in place instead.
- The loop over `self.model.model_computed_fields` strips every
  `@computed_field` (e.g. `Post.dedup_key`, `Author.dedup_key`,
  `Engagement.total_engagement`/`engagement_rate`, `Video.has_transcript`,
  `Conversation.display_title`). These are derived, in-process-only
  properties with **no matching database column** — `model_dump()` includes
  them by default because Pydantic v2 treats computed fields as part of the
  public serialized shape. Left in, PostgREST rejects the whole request with
  `"column ... not found in schema cache"`. Stripping them here, once,
  centrally, means no individual repository or call site has to remember to
  exclude them.
- `.pop(field_name, None)` (not `del`) — tolerates a computed-field name that
  for any reason isn't present in the dump (defensive, avoids a `KeyError`
  crashing every single write).

#### `_serialize_for_upsert` — the upsert-specific payload builder

```python
    def _serialize_for_upsert(self, obj: ModelT) -> dict[str, Any]:
        payload = self._serialize(obj)
        payload.pop("id", None)
        return payload
```

This is the method other modules' docstrings point to
(`app/ingestion/pipeline.py`, `app/embeddings/service.py`). Every domain model
uses `IdentifiedMixin`, which generates a fresh `id: uuid.UUID =
Field(default_factory=uuid.uuid4)` **client-side**, in Python, at object
construction time — not from the database. That means:

1. Scraping the same post twice (two separate pipeline runs, or two pages of
   the same run) produces **two different Python objects with two different
   random UUIDs**, even though they represent the same real-world record.
2. If those `id` values were sent as-is in an `upsert(...)`, Postgres would
   try to overwrite the *existing* row's primary key with the new random one
   on every re-ingestion of an already-seen natural key (e.g.
   `platform + platform_post_id`). This is rejected once any child row has a
   foreign key pointing at the old id — e.g. `comments.author_id`,
   `media.post_id`, `engagement.post_id` — because changing the parent's `id`
   would either violate the FK constraint or silently orphan children.
3. Dropping `id` from the payload before an upsert sidesteps this entirely:
   on `INSERT` (first time seeing this natural key), Postgres falls back to
   the column's default (`gen_random_uuid()` or similar) and assigns a fresh
   server-side id; on `UPDATE` (conflict on the natural key), Postgres simply
   never touches the `id` column because it's absent from the payload, so
   the **existing** row keeps its **original** id.

The direct, load-bearing consequence — spelled out in
`app/ingestion/pipeline.py` and `app/embeddings/service.py` — is that **the
`id` on a freshly-constructed (pre-persistence) Python model is never
trustworthy as a foreign key value once that object goes through
`upsert`/`bulk_upsert`.** Every downstream reference (a comment's
`author_id`, a video's `channel_id`, an embedding's `document_id`, etc.) must
be rebuilt from the **response** of the upsert call (which contains the
DB-authoritative ids), not from the objects that were sent in. This is exactly
what `app/ingestion/pipeline.py::_build_id_map` and
`app/embeddings/service.py::EmbeddingService.embed_batch` do — see "Shared
patterns" at the end of this document.

#### `_deserialize`

```python
    def _deserialize(self, row: dict[str, Any]) -> ModelT:
        return self.model.model_validate(row)
```

One-line wrapper around `self.model.model_validate(row)` used by every read
path. Kept as a named method (rather than inlined at each call site) so
subclasses could in principle override deserialization (e.g.
`EmbeddingRow._parse_pgvector_string` handles this at the field-validator
level instead, but the hook exists here too).

#### `get_by_id` / `require_by_id`

```python
    async def get_by_id(self, record_id: str) -> ModelT | None:
        def _run() -> Any:
            return self._table.select("*").eq("id", record_id).limit(1).execute()
        response = await asyncio.to_thread(_run)
        rows = response.data
        return self._deserialize(rows[0]) if rows else None
```

Fetch-by-primary-key, `None` on miss rather than raising — used wherever a
"maybe it exists" lookup is the natural shape (e.g.
`RetrievalService.popular_posts`/`_apply_filters` looking up a `Post` by id
and simply skipping it if it's gone).

```python
    async def require_by_id(self, record_id: str) -> ModelT:
        record = await self.get_by_id(record_id)
        if record is None:
            raise RecordNotFoundError(...)
        return record
```

The "I need this record or the caller has a bug/bad input" variant — used by
`ChatService.export_conversation` (`app/services/chat_service.py:47`) where a
missing conversation should surface as an error, not silently produce an
empty export.

#### `list_all` — the generic filtered/paginated read

```python
    async def list_all(
        self, *, filters=None, limit=100, offset=0, order_by=None,
        descending=True, include_deleted=False,
    ) -> list[ModelT]:
        def _run() -> Any:
            query = self._table.select("*")
            if not include_deleted and "deleted_at" in self.model.model_fields:
                query = query.is_("deleted_at", "null")
            for key, value in (filters or {}).items():
                query = query.eq(key, value)
            if order_by:
                query = query.order(order_by, desc=descending)
            return query.range(offset, offset + limit - 1).execute()
        response = await asyncio.to_thread(_run)
        return [self._deserialize(row) for row in response.data]
```

The workhorse behind almost every "find records matching X" method in every
concrete repository (`CommentRepository.by_post`, `PostRepository.by_author`,
`MentionRepository.by_username`, `ScrapeJobRepository.recent`, etc. — all of
them call this rather than building their own query). Design notes:

- All parameters are keyword-only (`*`) — with six optional parameters,
  positional calls would be an easy source of bugs (e.g. accidentally
  swapping `limit`/`offset`); forcing keywords makes every call site
  self-documenting.
- `if not include_deleted and "deleted_at" in self.model.model_fields:` — the
  soft-delete filter is applied **only** for models that actually have a
  `deleted_at` column (i.e. inherit `SoftDeleteMixin`: `Author`, `Channel`,
  `Video`, `Post`, `Comment`, `Conversation`). Checking
  `self.model.model_fields` at runtime (rather than requiring every
  repository to declare "soft-deletable: yes/no") means the base class
  automatically does the right thing for both kinds of table without any
  per-repository configuration or subclass override.
- `filters` is a flat `dict[str, Any]` of **equality-only** filters
  (`.eq(key, value)` for each). This is intentionally simple — anything
  needing a richer predicate (`ilike`, `gte`/`lte`, `text_search`, joins) is
  written as its own method with its own `_run()` closure in the concrete
  repository (see `ConversationRepository.search_by_title`,
  `PostRepository.posted_between`, `RetrievalService.keyword_search`) rather
  than trying to generalize the filter DSL here.
- `query.range(offset, offset + limit - 1)` — Postgres/PostgREST's `range` is
  an **inclusive** upper bound, hence `-1`; a naive `range(offset, offset +
  limit)` would fetch `limit + 1` rows.
- Returns `list[ModelT]`, always — an empty list on no matches, never `None`
  and never raises for "nothing found" (only `get_by_id`/`require_by_id`
  distinguish presence/absence).

#### `create`

```python
    async def create(self, obj: ModelT) -> ModelT:
        payload = self._serialize(obj)
        def _run() -> Any:
            return self._table.insert(payload).execute()
        try:
            response = await asyncio.to_thread(_run)
        except APIError as exc:
            if exc.code == "23505":  # unique_violation
                raise DuplicateRecordError(str(exc), context={"payload": payload}) from exc
            raise RepositoryError(str(exc), context={"payload": payload}) from exc
        logger.debug("Database insert", table=self.table_name, id=response.data[0].get("id"))
        return self._deserialize(response.data[0])
```

Plain insert, used for models that have no natural-key dedup concern — every
row really is new every time: `Conversation` (`ChatService.new_conversation`),
`ChatMessage` (`Assistant.ask` — both the user turn and the assistant turn),
`QueryLog`, `AssistantLog` (both in `Assistant.ask`), and `ScrapeJob`
(`ScrapeJobRepository.start`). Note it uses `_serialize` (which keeps the
client-generated `id`), not `_serialize_for_upsert` — for these append-only
tables the client-generated UUID *is* the row's real, final id; there's no
existing row it could collide with.
- Postgres error code `"23505"` is the standard `unique_violation` SQLSTATE
  code — mapped to the narrower `DuplicateRecordError` specifically so that
  is distinguishable from any other DB failure, though no current call site
  in `app/` actually branches on catching `DuplicateRecordError` separately
  from `RepositoryError` (both are allowed to propagate and get caught by
  the ingestion pipeline's blanket `except Exception` isolation).

#### `upsert` / `bulk_upsert` — the dedup workhorses

```python
    async def upsert(self, obj: ModelT, *, on_conflict: str) -> ModelT:
        payload = self._serialize_for_upsert(obj)
        def _run() -> Any:
            return self._table.upsert(payload, on_conflict=on_conflict).execute()
        ...
        return self._deserialize(response.data[0])

    async def bulk_upsert(self, objs: list[ModelT], *, on_conflict: str) -> list[ModelT]:
        if not objs:
            return []
        payloads = [self._serialize_for_upsert(o) for o in objs]
        def _run() -> Any:
            return self._table.upsert(payloads, on_conflict=on_conflict).execute()
        ...
        return [self._deserialize(row) for row in response.data]
```

- `on_conflict: str` is always **required, keyword-only, and passed by the
  caller** — `BaseRepository` itself has no opinion on what a table's natural
  key is; every concrete repository supplies its own (e.g.
  `"platform,platform_user_id"` for authors, `"source_id,source_type,model"`
  for embeddings). This keeps the generic base agnostic of schema while every
  concrete `*_repository.py` documents its own real unique constraint (which
  must match a DB-level `UNIQUE`/composite-PK constraint, or PostgREST's
  upsert has nothing to conflict against).
- `bulk_upsert` short-circuits on an empty list rather than sending a
  zero-row request to PostgREST — mainly for the ingestion pipeline, which
  calls this once per entity type per batch even when a given scrape result
  contains zero videos/comments/etc.
- One HTTP round trip handles the whole batch (`self._table.upsert(payloads,
  ...)` with `payloads` being a `list[dict]`) — this is why the docstring
  says "a scrape of thousands of posts is a handful of round trips, not
  one-per-row": `bulk_upsert` is *the* mechanism the ingestion pipeline uses
  to write each entity type in one call regardless of how many rows a scrape
  produced.
- Both log via `logger.debug(...)` with `table`, `on_conflict`, and `count` —
  the count is 1 for `upsert`, `len(payloads)` for `bulk_upsert`; useful for
  tracing exactly what a run wrote without instrumenting each concrete
  repository separately.
- Both re-raise as `RepositoryError` on `APIError` (no special
  `DuplicateRecordError` handling here — an upsert conflict is the *expected,
  successful* path, not an error, so there's nothing analogous to `create`'s
  `23505` branch).

#### `update` / `soft_delete`

```python
    async def update(self, record_id: str, data: dict[str, Any]) -> ModelT:
        def _run() -> Any:
            return self._table.update(data).eq("id", record_id).execute()
        ...
        if not response.data:
            raise RecordNotFoundError(f"{self.model.__name__} {record_id} not found")
        ...
        return self._deserialize(response.data[0])
```

Partial update by raw `dict` (not a full model) — deliberately loose-typed
because callers only ever want to touch one or two fields (a job's `status`,
a comment's `parent_comment_id`, a conversation's `is_archived`) and building
a full `ModelT` just to patch one column would require re-fetching the whole
row first. `response.data` being empty (rather than an exception) is
PostgREST's signal that the `eq("id", ...)` filter matched nothing — the
method translates that into `RecordNotFoundError` explicitly, since a caller
updating a specific id expects that id to exist.

```python
    async def soft_delete(self, record_id: str) -> None:
        await self.update(record_id, {"deleted_at": datetime.now(UTC).isoformat()})
```

Sets `deleted_at` rather than issuing a real `DELETE` — preserves the row (and
anything referencing it) for audit/undo, while `list_all`'s default
`include_deleted=False` filter means soft-deleted rows disappear from normal
reads automatically. Used by `ChatService.clear_conversation`
(`app/services/chat_service.py:43`).

#### `count`

```python
    async def count(self, filters: dict[str, Any] | None = None) -> int:
        def _run() -> Any:
            query = self._table.select("id", count="exact")
            for key, value in (filters or {}).items():
                query = query.eq(key, value)
            return query.execute()
        response = await asyncio.to_thread(_run)
        return response.count or 0
```

`select("id", count="exact")` asks PostgREST for a row count via the
`Content-Range` response header rather than fetching and counting full rows
client-side — cheap even over a large table. `response.count or 0` guards the
theoretical `None` case. Used directly by `AnalyticsService.total_posts`,
`.total_comments`, and `.platform_distribution` (`app/services/analytics_service.py:45,48,52`),
which fires one `count()` per `PlatformName` concurrently via `asyncio.gather`.

### Where `BaseRepository` itself is referenced

- `app/repositories/__init__.py` — re-exported (`from app.repositories.base
  import BaseRepository`).
- Every concrete repository in `app/repositories/*.py` — subclasses it.
- `docs/architecture.md:64` — mentioned as the shared base for the repository
  layer in the architecture overview.
- `tests/unit/test_repositories.py` — direct unit tests of `_serialize` /
  `_serialize_for_upsert` (the file's own docstring calls this "the single
  most important behavior under test").
- `tests/integration/test_ingestion_pipeline.py` — a fake in-memory repository
  used by the pipeline tests explicitly mirrors `_serialize_for_upsert`'s
  "drop id, keep existing row's id on conflict" behavior (see its docstring
  at the top of the file) to validate `_build_id_map` end-to-end.

---

## `app/repositories/__init__.py`

```python
from app.repositories.author_repository import AuthorRepository
from app.repositories.base import BaseRepository
from app.repositories.channel_repository import ChannelRepository, VideoRepository
from app.repositories.comment_repository import CommentRepository
from app.repositories.conversation_repository import ConversationRepository
from app.repositories.embedding_repository import (
    Document, DocumentRepository, EmbeddingRepository, EmbeddingRow,
)
from app.repositories.engagement_repository import EngagementRepository
from app.repositories.hashtag_repository import HashtagRepository, PostHashtagRepository
from app.repositories.media_repository import MediaRepository
from app.repositories.mention_repository import MentionRepository
from app.repositories.message_repository import MessageRepository
from app.repositories.platform_repository import PlatformRepository
from app.repositories.post_repository import PostRepository
from app.repositories.query_log_repository import AssistantLogRepository, QueryLogRepository
from app.repositories.scrape_job_repository import ScrapeJob, ScrapeJobRepository

__all__ = [ ... ]  # 18 names, one per class above
```

Pure re-export barrel — the entire file's purpose is to let calling code write
`from app.repositories import PostRepository, AuthorRepository` instead of
one import line per submodule. It re-exports:

- 16 repository classes (one per table-mapped concept, `ChannelRepository`
  and `VideoRepository` both live in `channel_repository.py`;
  `HashtagRepository`/`PostHashtagRepository` both live in
  `hashtag_repository.py`; `QueryLogRepository`/`AssistantLogRepository` both
  live in `query_log_repository.py`).
- Two Pydantic *models* that aren't defined in `app/models/pydantic/` at all
  and only exist inside the repositories package: `Document`/`EmbeddingRow`
  (in `embedding_repository.py`, mirroring the `documents`/`embeddings`
  tables — see `docs/embedding_model_explained.md`) and `ScrapeJob` (in
  `scrape_job_repository.py`, mirroring `scrape_jobs`). These three classes
  don't fit the "one unified cross-platform domain model" pattern the rest of
  `app/models/pydantic/` follows, so they're kept next to the repository that
  owns their table instead.

`__all__` is spelled out explicitly (not just re-import) so `from
app.repositories import *` and static analyzers/IDEs get a clean, complete
export list rather than only unstarred-by-default guesses. Every name in
`__all__` is exercised by at least one non-repository call site elsewhere in
`app/` (see each repository's own section below) or by
`tests/unit/test_repositories.py`/`test_repositories_extended.py`.

---

## `app/repositories/author_repository.py`

```python
from __future__ import annotations
import asyncio
from typing import Any
from app.models.pydantic import Author
from app.repositories.base import BaseRepository

class AuthorRepository(BaseRepository[Author]):
    table_name = "authors"
    model = Author
```

`Author` (`app/models/pydantic/author.py`) is the cross-platform "who posted
this" model; `dedup_key` is `f"{platform}:{platform_user_id}"` (a
`@computed_field`, stripped by `_serialize` before any DB write).

```python
    async def get_by_platform_user_id(self, platform: str, platform_user_id: str) -> Author | None:
        def _run() -> Any:
            return (
                self._table.select("*").eq("platform", platform)
                .eq("platform_user_id", platform_user_id).limit(1).execute()
            )
        response = await asyncio.to_thread(_run)
        rows = response.data
        return self._deserialize(rows[0]) if rows else None
```

Direct natural-key lookup, hand-rolled (rather than `list_all(filters=...)`)
only because it needs `Any` typing consistent with the rest of the file's
style; functionally equivalent to `list_all(filters={"platform": platform,
"platform_user_id": platform_user_id}, limit=1)`. This exact method is
currently **not called anywhere in `app/`** outside its own definition —
grepping the whole tree finds no call site in `app/ingestion`, `app/services`,
`app/retrieval`, or `app/ai`; it is exercised only by
`tests/unit/test_repositories*.py`. (The ingestion pipeline instead always
goes through `bulk_upsert_authors`, which returns the persisted rows directly
— it never needs a separate existence check.)

```python
    async def upsert_author(self, author: Author) -> Author:
        return await self.upsert(author, on_conflict="platform,platform_user_id")

    async def bulk_upsert_authors(self, authors: list[Author]) -> list[Author]:
        return await self.bulk_upsert(authors, on_conflict="platform,platform_user_id")
```

`on_conflict="platform,platform_user_id"` matches the `authors` table's
composite unique constraint (migrations/0002) — this is how "same person
scraped again in a later run" gets merged into one row instead of
duplicated. `bulk_upsert_authors` is the one actually driving production
traffic: called from `IngestionPipeline._run`
(`app/ingestion/pipeline.py:144-146`) via `self._safe_bulk(
self.author_repo.bulk_upsert_authors, authors, report, "author")`, and its
result (`persisted_authors`) feeds directly into `_build_id_map` to produce
`author_id_map`, which every other entity type in the same run (`channels`,
`posts`, `comments`) uses to remap its own client-generated `author_id`
reference to the real, DB-persisted author id.

```python
    async def most_active(self, platform: str | None = None, limit: int = 10) -> list[Author]:
        filters = {"platform": platform} if platform else None
        return await self.list_all(
            filters=filters, order_by="post_count", descending=True, limit=limit
        )
```

Thin wrapper over `list_all`, ordering by the `post_count` column (populated
by the normalization layer, not computed live). Called by
`AnalyticsService.most_active_authors` (`app/services/analytics_service.py:57`),
which backs the Gradio analytics dashboard's "most active authors" widget
(`AnalyticsService.dashboard_summary`, `app/services/analytics_service.py:76-106`).

### Where `AuthorRepository` is used elsewhere

- `app/ingestion/pipeline.py:29,92,105,144-148` — constructed in
  `IngestionPipeline.__init__` (defaulted if not injected), drives the
  author-upsert step of every ingestion run.
- `app/services/analytics_service.py:16,30,38,56-57` — `most_active`.
- `app/retrieval/service.py:17,39,45,235` — constructed in
  `RetrievalService.__init__`; used in `_apply_filters` via
  `self.author_repo.get_by_id(post.author_id)` to resolve `filters.author_username`
  against a post's real author row.
- `tests/unit/test_repositories.py`, `tests/unit/test_repositories_extended.py`,
  `tests/integration/test_ingestion_pipeline.py` — unit/integration coverage.

---

## `app/repositories/channel_repository.py`

Two repositories in one file — `Channel` and `Video` are modeled together
(`app/models/pydantic/channel.py`) because they're both YouTube-shaped
concepts with no equivalent shape on Instagram/Twitter, so their repositories
are kept adjacent for the same reason.

```python
from __future__ import annotations
import asyncio
from typing import Any
from app.models.pydantic import Channel, Video
from app.repositories.base import BaseRepository

class ChannelRepository(BaseRepository[Channel]):
    table_name = "channels"
    model = Channel
```

```python
    async def get_by_platform_channel_id(self, platform, platform_channel_id) -> Channel | None:
        ...  # same eq/eq/limit(1) shape as AuthorRepository.get_by_platform_user_id
```

Not called anywhere in `app/` (grep confirms zero references outside its own
definition and tests) — same situation as `AuthorRepository.get_by_platform_user_id`:
the pipeline relies entirely on `bulk_upsert_channels`'s response, never a
separate existence check.

```python
    async def upsert_channel(self, channel: Channel) -> Channel:
        return await self.upsert(channel, on_conflict="platform,platform_channel_id")

    async def bulk_upsert_channels(self, channels: list[Channel]) -> list[Channel]:
        return await self.bulk_upsert(channels, on_conflict="platform,platform_channel_id")
```

`on_conflict="platform,platform_channel_id"` — `Channel.dedup_key` is
`f"{platform}:{platform_channel_id}"`, matching this constraint one-to-one.
`bulk_upsert_channels` is called from `IngestionPipeline._run`
(`app/ingestion/pipeline.py:150-159`) after channels have had their
`author_id` remapped via `author_id_map`; its response feeds `channel_id_map`,
used to remap `Video.channel_id` further down the same run.

```python
    async def by_author(self, author_id: str) -> list[Channel]:
        return await self.list_all(filters={"author_id": author_id}, limit=50)
```

Not called anywhere in `app/` outside tests (grep confirms) — a
"channels owned by this author" convenience query with no current production
call site; exists for symmetry with `PostRepository.by_author` /
`VideoRepository.by_channel` and is exercised only by
`tests/unit/test_repositories_extended.py`.

```python
class VideoRepository(BaseRepository[Video]):
    table_name = "videos"
    model = Video

    async def get_by_platform_video_id(self, platform, platform_video_id) -> Video | None: ...
    async def upsert_video(self, video: Video) -> Video:
        return await self.upsert(video, on_conflict="platform,platform_video_id")
    async def bulk_upsert_videos(self, videos: list[Video]) -> list[Video]:
        return await self.bulk_upsert(videos, on_conflict="platform,platform_video_id")
    async def by_channel(self, channel_id: str, *, limit: int = 100) -> list[Video]:
        return await self.list_all(
            filters={"channel_id": channel_id}, order_by="published_at",
            descending=True, limit=limit,
        )
```

Mirrors `ChannelRepository` exactly, one level down the YouTube hierarchy.
`get_by_platform_video_id` and `by_channel` are, again, not called anywhere in
`app/` outside tests. `bulk_upsert_videos` **is** live: called from
`IngestionPipeline._run` (`app/ingestion/pipeline.py:172-185`) after
`channel_id`/`post_id` remapping, feeding
`IngestionPipeline._generate_embeddings` (`app/ingestion/pipeline.py:209-210,349-358`)
which pulls each persisted video's `transcript` field for embedding.

### Where `ChannelRepository`/`VideoRepository` are used elsewhere

- `app/ingestion/pipeline.py:30,93-94,106-107,150-185` — both constructed in
  `IngestionPipeline.__init__`; both `bulk_upsert_*` methods drive real
  ingestion writes.
- `tests/unit/test_repositories_extended.py`, `tests/integration/test_ingestion_pipeline.py`.

---

## `app/repositories/comment_repository.py`

```python
from __future__ import annotations
import asyncio
from typing import Any
from app.models.pydantic import Comment
from app.repositories.base import BaseRepository

class CommentRepository(BaseRepository[Comment]):
    table_name = "comments"
    model = Comment

    async def get_by_platform_comment_id(self, platform, platform_comment_id) -> Comment | None:
        ...  # not called outside tests (grep confirms)

    async def upsert_comment(self, comment: Comment) -> Comment:
        return await self.upsert(comment, on_conflict="platform,platform_comment_id")

    async def bulk_upsert_comments(self, comments: list[Comment]) -> list[Comment]:
        return await self.bulk_upsert(comments, on_conflict="platform,platform_comment_id")

    async def by_post(self, post_id: str, *, limit: int = 200) -> list[Comment]:
        return await self.list_all(filters={"post_id": post_id}, limit=limit)

    async def replies_to(self, parent_comment_id: str, *, limit: int = 200) -> list[Comment]:
        return await self.list_all(filters={"parent_comment_id": parent_comment_id}, limit=limit)
```

`Comment.dedup_key` is `f"{platform}:{platform_comment_id}"`
(`app/models/pydantic/comment.py`), matching `on_conflict`.
`bulk_upsert_comments` is the production write path: called from
`IngestionPipeline._run` (`app/ingestion/pipeline.py:187-202`) after comments
have had `post_id`/`author_id` remapped (via `post_id_map`/`author_id_map`)
and `parent_comment_id` deliberately zeroed for the first upsert pass
(`"parent_comment_id": None,  # linked in a second pass below`). The **base
class's** plain `update` method — not a `CommentRepository`-specific method —
is then used directly by
`IngestionPipeline._relink_comment_parents` (`app/ingestion/pipeline.py:213-228`,
call at line 226: `await self.comment_repo.update(new_id, {"parent_comment_id":
new_parent_id})`) to point replies at their parent's real, persisted id in a
second pass, once every comment in the batch has been assigned one. This is
the reason comments are inserted with `parent_comment_id` blanked out first:
a reply's parent might be in the *same* batch and not yet have a persisted id
when the first bulk upsert runs.

`by_post` and `replies_to` are both defined but, per grep, **not called
anywhere in `app/`** — only `tests/unit/test_repositories_extended.py`
exercises them directly. (Contrast with `MediaRepository.by_post` and
`MentionRepository.by_post`, which the pipeline *does* call — see below.)
`get_by_platform_comment_id` is likewise test-only.

### Where `CommentRepository` is used elsewhere

- `app/ingestion/pipeline.py:31,96,109,187-203,213-228` — `bulk_upsert_comments`
  and `update` (via `_relink_comment_parents`).
- `app/services/analytics_service.py:17,29,37,44,47-48` — constructed in
  `AnalyticsService.__init__`; `AnalyticsService.total_comments` calls the
  **inherited** `count()`, not any `CommentRepository`-specific method.
- `tests/unit/test_repositories.py`, `tests/unit/test_repositories_extended.py`,
  `tests/integration/test_ingestion_pipeline.py`.

---

## `app/repositories/conversation_repository.py`

```python
from __future__ import annotations
import asyncio
from typing import Any
from app.models.pydantic import Conversation
from app.repositories.base import BaseRepository

class ConversationRepository(BaseRepository[Conversation]):
    table_name = "conversations"
    model = Conversation
```

`Conversation` (`app/models/pydantic/conversation.py`) has no natural-key
dedup story — it's a chat session, created fresh every time a user starts a
new conversation — so this repository has **no `upsert`/`bulk_upsert` method
at all**, unlike every content-entity repository above. Every write goes
through the inherited `create`/`update`/`soft_delete`.

```python
    async def by_user(self, user_id: str, *, limit: int = 50) -> list[Conversation]:
        return await self.list_all(
            filters={"user_id": user_id}, order_by="updated_at", descending=True, limit=limit
        )
```

Not called anywhere in `app/` outside tests — a "list this user's
conversations" query with no wired-up caller yet (the current chat UI/service
lists *all* conversations via `list_all` directly, see below — user-scoping
isn't yet exposed through `ChatService`).

```python
    async def search_by_title(self, query: str, *, limit: int = 20) -> list[Conversation]:
        """Fuzzy title search backing the Gradio "search conversations" feature."""
        def _run() -> Any:
            return (
                self._table.select("*").is_("deleted_at", "null")
                .ilike("title", f"%{query}%").order("updated_at", desc=True)
                .limit(limit).execute()
            )
        response = await asyncio.to_thread(_run)
        return [self._deserialize(row) for row in response.data]
```

Hand-rolled (not `list_all`) because `list_all`'s filter dict only supports
`.eq()`; a substring match needs `.ilike()`, which `list_all` doesn't expose.
Manually re-applies the same `deleted_at IS NULL` soft-delete filter
`list_all` would have applied automatically. Called by
`ChatService.search_conversations` (`app/services/chat_service.py:36-37`),
which is the direct backing for the Gradio chat UI's conversation search box.

```python
    async def archive(self, conversation_id: str) -> Conversation:
        return await self.update(conversation_id, {"is_archived": True})
```

Not called anywhere in `app/` — `ChatService` currently only exposes
`clear_conversation` (which soft-deletes, via the inherited `soft_delete`),
not an archive action; this method has no wired-up caller yet outside
`tests/unit/test_repositories_extended.py`.

### Where `ConversationRepository` is used elsewhere

- `app/services/chat_service.py:10,18,22,29,32-34,37,43,47` — constructed in
  `ChatService.__init__`; `create` (new conversation), `list_all` (list
  conversations, ordered by `updated_at`), `search_by_title`, `soft_delete`
  (clear), `require_by_id` (export).
- `app/ai/assistant.py:27,90,100,119` — constructed in `Assistant.__init__`;
  `create` is called when `Assistant.ask` is invoked without an existing
  `conversation_id`, auto-starting a new conversation titled from the first
  60 characters of the question.
- `tests/unit/test_repositories_extended.py`.

---

## `app/repositories/embedding_repository.py`

Full field-by-field and runtime-flow documentation of this file already
exists in **`docs/embedding_model_explained.md` (§3b and §2)** — including why
there are two classes/tables instead of one, and the `_parse_pgvector_string`
validator. This section is a concise summary plus the call-site trace, kept
here for completeness and consistency with the rest of this document.

```python
class Document(IdentifiedMixin, CreatedAtMixin, BaseSchema):
    source_type: EmbeddingSourceType
    source_id: str
    platform: PlatformName
    content: str
    metadata: dict = Field(default_factory=dict)
```

Mirrors the `documents` table — the human-readable, keyword-searchable
source text. `CreatedAtMixin` (not `TimestampMixin`): the table has no
`updated_at` trigger.

```python
class EmbeddingRow(BaseModel):
    document_id: str
    source_type: EmbeddingSourceType
    source_id: str
    platform: PlatformName
    model: str
    dimensions: int
    checksum: str
    vector: list[float]
    metadata: dict = Field(default_factory=dict)

    @field_validator("vector", mode="before")
    @classmethod
    def _parse_pgvector_string(cls, value: Any) -> Any:
        if isinstance(value, str):
            return [float(x) for x in value.strip("[]").split(",")]
        return value
```

Mirrors the `embeddings` table. Note it's a plain `BaseModel`, **not** a
`BaseSchema` subclass — it doesn't need `str_strip_whitespace`/`extra="ignore"`
since it's only ever built internally by `EmbeddingService`, never from raw
scraped/user input. `_parse_pgvector_string` exists because PostgREST returns
the `pgvector` column as its Postgres array-literal string form
(`"[0.01,-0.02,...]"`) rather than a JSON array on read-back after an
insert/upsert — without this, `list[float]` validation would fail on every
round trip.

```python
class DocumentRepository(BaseRepository[Document]):
    table_name = "documents"
    model = Document

    async def get_by_source(self, source_type: str, source_id: str) -> Document | None:
        results = await self.list_all(filters={"source_type": source_type, "source_id": source_id}, limit=1)
        return results[0] if results else None

    async def upsert_document(self, document: Document) -> Document:
        return await self.upsert(document, on_conflict="source_type,source_id")
```

`get_by_source` is not called anywhere in `app/` outside tests. The
production write path uses the **inherited** `bulk_upsert` directly (not
`upsert_document`): `app/embeddings/service.py:101-103` calls
`self.document_repo.bulk_upsert(documents, on_conflict="source_type,source_id")`.

```python
class EmbeddingRepository(BaseRepository[EmbeddingRow]):
    table_name = "embeddings"
    model = EmbeddingRow

    async def get_by_checksum(self, source_id, source_type, model) -> EmbeddingRow | None: ...
    async def upsert_embedding(self, embedding: EmbeddingRow) -> EmbeddingRow:
        return await self.upsert(embedding, on_conflict="source_id,source_type,model")
    async def bulk_upsert_embeddings(self, embeddings: list[EmbeddingRow]) -> list[EmbeddingRow]:
        return await self.bulk_upsert(embeddings, on_conflict="source_id,source_type,model")
    async def match(self, query_vector, *, match_count=10, platform=None) -> list[dict[str, Any]]: ...
```

- `get_by_checksum` — called by `EmbeddingService.embed_batch`
  (`app/embeddings/service.py:73-76`) to decide, per item, whether the
  content changed since last time (skip re-embedding if the stored checksum
  matches). `upsert_embedding` (singular) is not called anywhere in `app/`;
  production always goes through `bulk_upsert_embeddings`
  (`app/embeddings/service.py:130`).
- `match` — wraps the `match_embeddings` Postgres RPC (cosine similarity
  computed in the database). Called by `RetrievalService.semantic_search`
  (`app/retrieval/service.py:104`), which is itself called by
  `RetrievalService.hybrid_search` (`app/retrieval/service.py:136`) and,
  transitively, by `Assistant.ask` (`app/ai/assistant.py:147`).

### Where `EmbeddingRepository`/`DocumentRepository` are used elsewhere

- `app/embeddings/service.py:18-23,51,55-56,73-76,101-103,130` — the whole
  write path (`EmbeddingService.embed_batch`).
- `app/retrieval/service.py:18,37,43,104` — `EmbeddingRepository.match` via
  `RetrievalService.semantic_search`.
- `app/repositories/__init__.py` — re-exported.
- `tests/unit/test_embeddings.py`, `tests/unit/test_repositories_extended.py`.

---

## `app/repositories/engagement_repository.py`

```python
from __future__ import annotations
from app.models.pydantic import Engagement
from app.repositories.base import BaseRepository

class EngagementRepository(BaseRepository[Engagement]):
    table_name = "engagement"
    model = Engagement
```

No `asyncio`/`Any` import here (unlike most other repositories) because none
of this file's methods hand-roll a custom `_run()` closure — every method
delegates entirely to base-class helpers (`list_all`/`upsert`). `Engagement`
(`app/models/pydantic/engagement.py`) has no natural `dedup_key` of its own
(no `@computed_field` for it) because it dedups on a single plain column,
`post_id`, rather than a composite platform-native id.

```python
    async def get_by_post(self, post_id: str) -> Engagement | None:
        results = await self.list_all(filters={"post_id": post_id}, limit=1)
        return results[0] if results else None
```

Called by `RetrievalService._apply_filters` (`app/retrieval/service.py:239`)
when a query has a `min_likes` filter — fetches the engagement row for a
candidate post to check its like count against the threshold.

```python
    async def upsert_for_post(self, engagement: Engagement) -> Engagement:
        return await self.upsert(engagement, on_conflict="post_id")
```

`on_conflict="post_id"` — one engagement row per post, so simple equality on
the FK column is the entire natural key (no composite platform+native-id
needed, since `post_id` already uniquely identifies the parent). Called by
`IngestionPipeline._ingest_engagement` (`app/ingestion/pipeline.py:301-318`),
which builds the `Engagement` object via the platform-specific normalizer's
`extract_engagement(post)` and re-points it at the post's *persisted* id
before upserting — another instance of the "never trust the pre-upsert id"
pattern.

```python
    async def top_by_likes(self, *, limit: int = 10) -> list[Engagement]:
        return await self.list_all(
            filters=None, order_by="likes", descending=True, limit=limit, include_deleted=True
        )
```

`include_deleted=True` here is actually a no-op/documentation nicety rather
than a functional override: `Engagement` doesn't inherit `SoftDeleteMixin` (it
inherits only `IdentifiedMixin, TimestampMixin, BaseSchema`), so
`list_all`'s `"deleted_at" in self.model.model_fields` guard already skips
the filter regardless of this flag — the explicit `True` just makes the
intent ("engagement rows are never soft-deleted") visible at the call site.
Called by `AnalyticsService.top_engagement_posts`
(`app/services/analytics_service.py:62-63`, feeding the dashboard) and by
`RetrievalService.popular_posts` (`app/retrieval/service.py:175`, backing
"most liked posts" style questions with no keyword/semantic query).

### Where `EngagementRepository` is used elsewhere

- `app/ingestion/pipeline.py:32,101,114,301-318` — `upsert_for_post`.
- `app/services/analytics_service.py:18,31,39,62-63` — `top_by_likes`.
- `app/retrieval/service.py:19,40,46,175,239` — `top_by_likes`, `get_by_post`.
- `tests/unit/test_repositories_extended.py`.

---

## `app/repositories/hashtag_repository.py`

Two repositories in one file: `HashtagRepository` (the canonical `hashtags`
table, one row per unique tag) and `PostHashtagRepository` (the
many-to-many `post_hashtags` join table).

```python
from __future__ import annotations
import asyncio
from typing import Any
from app.models.pydantic import Hashtag, PostHashtag
from app.repositories.base import BaseRepository

class HashtagRepository(BaseRepository[Hashtag]):
    table_name = "hashtags"
    model = Hashtag

    async def get_by_tag(self, tag: str) -> Hashtag | None:
        results = await self.list_all(filters={"tag": tag.lstrip("#").lower()}, limit=1)
        return results[0] if results else None
```

Normalizes the input the same way `Hashtag._normalize_tag`
(`app/models/pydantic/hashtag.py`) does (`lstrip("#")` + lowercase) so a
lookup for `"#Python"` matches a stored `"python"` row. Not called anywhere in
`app/` outside tests — the ingestion pipeline never looks up a single
hashtag, only bulk-upserts.

```python
    async def upsert_tag(self, hashtag: Hashtag) -> Hashtag:
        return await self.upsert(hashtag, on_conflict="tag")

    async def bulk_upsert_tags(self, hashtags: list[Hashtag]) -> list[Hashtag]:
        return await self.bulk_upsert(hashtags, on_conflict="tag")
```

`on_conflict="tag"` — the tag string itself is the natural key (already
normalized by the model's field validator before it ever reaches
serialization). `bulk_upsert_tags` is called by
`IngestionPipeline._ingest_hashtags` (`app/ingestion/pipeline.py:250-262`):
collects the set of unique tags across every post in the batch, upserts them
all in one call, then builds `tag_id_map` from the response to resolve each
tag string to its persisted `Hashtag.id` for linking.

```python
    async def trending(self, *, limit: int = 20) -> list[dict[str, Any]]:
        """Count posts per hashtag via the `post_hashtags` join table. ..."""
        def _run() -> Any:
            return (
                self._table.select("id, tag").order("created_at", desc=True).limit(limit).execute()
            )
        response = await asyncio.to_thread(_run)
        return response.data
```

Despite the docstring describing a join-table count, the implementation as
written only selects `id, tag` ordered by `created_at` from `hashtags`
itself — it does **not** actually join/count `post_hashtags` in this version
of the code (there's a gap between the documented intent and the current
query; worth flagging if trending counts look wrong in the dashboard). It
returns raw `dict`s (`response.data`), not deserialized `Hashtag` models,
because the docstring's intended shape (`tag`, count) doesn't match the
`Hashtag` model's fields. Called by `AnalyticsService.trending_hashtags`
(`app/services/analytics_service.py:59-60`), feeding the dashboard's
"trending hashtags" widget.

```python
class PostHashtagRepository(BaseRepository[PostHashtag]):
    table_name = "post_hashtags"
    model = PostHashtag

    async def link(self, post_id: str, hashtag_id: str) -> None:
        payload = {"post_id": post_id, "hashtag_id": hashtag_id}
        def _run() -> Any:
            return self._table.upsert(payload, on_conflict="post_id,hashtag_id").execute()
        await asyncio.to_thread(_run)
```

Single-link convenience method, bypassing `_serialize`/`_serialize_for_upsert`
entirely (builds the payload dict by hand since `PostHashtag` has no `id`
field to worry about — it's a plain `BaseSchema`, not `IdentifiedMixin`).
Not called anywhere in `app/` outside tests — production always links in
bulk.

```python
    async def bulk_link(self, links: list[PostHashtag]) -> None:
        if not links:
            return
        payloads = [self._serialize(link) for link in links]
        def _run() -> Any:
            return self._table.upsert(payloads, on_conflict="post_id,hashtag_id").execute()
        await asyncio.to_thread(_run)
```

Uses `self._serialize` (not `_serialize_for_upsert`) because `PostHashtag`
has no `id` field at all to strip — it's a pure join row keyed by the
`(post_id, hashtag_id)` pair, so there's nothing for `_serialize_for_upsert`'s
extra `.pop("id", None)` to do. Called by
`IngestionPipeline._ingest_hashtags` (`app/ingestion/pipeline.py:264-277`) to
write every post-to-hashtag link for a batch in one round trip, after both
posts and hashtags have persisted ids.

```python
    async def hashtags_for_post(self, post_id: str) -> list[PostHashtag]:
        return await self.list_all(filters={"post_id": post_id}, limit=500)
```

Not called anywhere in `app/` outside tests.

### Where `HashtagRepository`/`PostHashtagRepository` are used elsewhere

- `app/ingestion/pipeline.py:33,98,111-112,250-277` — `bulk_upsert_tags`,
  `bulk_link`.
- `app/services/analytics_service.py:19,32,40,59-60` — `trending`.
- `tests/unit/test_repositories_extended.py`.

---

## `app/repositories/media_repository.py`

```python
from __future__ import annotations
import asyncio
from app.models.pydantic import Media
from app.repositories.base import BaseRepository

class MediaRepository(BaseRepository[Media]):
    table_name = "media"
    model = Media

    async def by_post(self, post_id: str) -> list[Media]:
        return await self.list_all(filters={"post_id": post_id}, limit=500)
```

`Media` (`app/models/pydantic/media.py`) has no `dedup_key`/upsert method at
all — media rows are deduplicated by **URL comparison in Python**, not by a
DB-level natural key/upsert. `by_post` is the mechanism: called by
`IngestionPipeline._ingest_media` (`app/ingestion/pipeline.py:230-248`) to
fetch a post's *already-persisted* media rows, build a set of their URLs
(`existing_urls`), and filter the post's freshly-scraped `media` list down to
only genuinely new URLs before inserting — this is how the same post being
scraped twice doesn't duplicate its image/video attachments even though
`Media` has no unique constraint to upsert against.

```python
    async def bulk_create_media(self, media_items: list[Media]) -> list[Media]:
        if not media_items:
            return []
        def _run() -> list[dict]:
            payload = [self._serialize(m) for m in media_items]
            return self._table.insert(payload).execute().data
        rows = await asyncio.to_thread(_run)
        return [self._deserialize(row) for row in rows]
```

Uses `.insert(...)`, not `.upsert(...)` — consistent with "dedup already
happened in Python via `by_post` + URL comparison before this is ever
called," so there's nothing to conflict against; a true `upsert` would need a
DB unique constraint this table doesn't have. Called immediately after
`by_post` in `IngestionPipeline._ingest_media`
(`app/ingestion/pipeline.py:245-246`).

### Where `MediaRepository` is used elsewhere

- `app/ingestion/pipeline.py:34,97,110,230-248` — `by_post`, `bulk_create_media`.
- `tests/unit/test_repositories_extended.py`.

Note: `Post.media` (the in-memory `list[Media]` carried on a scraped `Post`
object) is declared with `exclude=True` in `app/models/pydantic/post.py:32` —
it never reaches `PostRepository`'s own serialized payload; `MediaRepository`
is the only repository that ever persists media rows, always via the
separate flow above.

---

## `app/repositories/mention_repository.py`

```python
from __future__ import annotations
import asyncio
from app.models.pydantic import Mention
from app.repositories.base import BaseRepository

class MentionRepository(BaseRepository[Mention]):
    table_name = "mentions"
    model = Mention

    async def by_post(self, post_id: str) -> list[Mention]:
        return await self.list_all(filters={"post_id": post_id}, limit=500)

    async def by_comment(self, comment_id: str) -> list[Mention]:
        return await self.list_all(filters={"comment_id": comment_id}, limit=500)
```

Structurally identical dedup story to `MediaRepository`: `Mention`
(`app/models/pydantic/hashtag.py`) has no upsert method; dedup happens by
comparing usernames in Python. `by_post` is called by
`IngestionPipeline._ingest_mentions` (`app/ingestion/pipeline.py:279-299`) to
fetch existing mentions for a post, build `existing_usernames`, and filter
`post.mentions` down to genuinely new usernames. `by_comment` is not called
anywhere in `app/` outside tests (mentions are currently only ingested at the
post level, not per-comment, despite the model supporting `comment_id`).

```python
    async def by_username(self, username: str, *, limit: int = 100) -> list[Mention]:
        return await self.list_all(filters={"username": username.lstrip("@").lower()}, limit=limit)
```

Normalizes input the same way `Mention._normalize_username` does. Not called
anywhere in `app/` outside tests.

```python
    async def bulk_create_mentions(self, mentions: list[Mention]) -> list[Mention]:
        if not mentions:
            return []
        def _run() -> list[dict]:
            payload = [self._serialize(m) for m in mentions]
            return self._table.insert(payload).execute().data
        rows = await asyncio.to_thread(_run)
        return [self._deserialize(row) for row in rows]
```

Same `.insert()`-not-`.upsert()` shape as `MediaRepository.bulk_create_media`,
for the same reason. Called by `IngestionPipeline._ingest_mentions`
(`app/ingestion/pipeline.py:294`).

### Where `MentionRepository` is used elsewhere

- `app/ingestion/pipeline.py:35,100,113,279-299` — `by_post`, `bulk_create_mentions`.
- `tests/unit/test_repositories_extended.py`.

---

## `app/repositories/message_repository.py`

```python
from __future__ import annotations
from app.models.pydantic import ChatMessage
from app.repositories.base import BaseRepository

class MessageRepository(BaseRepository[ChatMessage]):
    table_name = "messages"
    model = ChatMessage

    async def by_conversation(self, conversation_id: str, *, limit: int = 200) -> list[ChatMessage]:
        return await self.list_all(
            filters={"conversation_id": conversation_id}, order_by="created_at",
            descending=False, limit=limit, include_deleted=True,
        )
```

The single method on this repository beyond the inherited base. Two things
worth calling out:

- `descending=False` — messages are returned **oldest-first**, unlike almost
  every other `list_all`-based method in the app (which default to
  newest-first). This matters because both real callers reconstruct a
  chronological transcript: `Assistant.ask` builds prompt history from it
  (`app/ai/assistant.py:122-123`), and `ChatService.export_conversation`
  renders a Markdown transcript in reading order (`app/services/chat_service.py:39-40,48-57`).
- `include_deleted=True` — like `EngagementRepository.top_by_likes`, this is
  effectively a no-op: `ChatMessage` inherits `CreatedAtMixin`, not
  `SoftDeleteMixin`, so it has no `deleted_at` field for `list_all`'s guard to
  find regardless. Included for the same "state the invariant explicitly"
  reason.

`ChatMessage` (`app/models/pydantic/conversation.py`) has no upsert method —
every message is a genuinely new, append-only row, written via the
**inherited** `create`, never `MessageRepository`-specific.

### Where `MessageRepository` is used elsewhere

- `app/services/chat_service.py:11,19,23,39-40` — constructed in
  `ChatService.__init__`; `by_conversation` for `get_history`.
- `app/ai/assistant.py:28,91,101,122-127,192-204` — constructed in
  `Assistant.__init__`; `by_conversation` (load history) and `create` (write
  the user turn, then the assistant turn) both inside `Assistant.ask`.
- `tests/unit/test_repositories_extended.py`.

---

## `app/repositories/platform_repository.py`

```python
from __future__ import annotations
from app.models.pydantic import Platform
from app.repositories.base import BaseRepository

class PlatformRepository(BaseRepository[Platform]):
    table_name = "platforms"
    model = Platform

    async def get_by_name(self, name: str) -> Platform | None:
        results = await self.list_all(filters={"name": name}, limit=1)
        return results[0] if results else None
```

The smallest repository in the package — one method beyond the inherited
base, backing the `platforms` reference table (`Platform` —
`app/models/pydantic/platform.py` — is just `name`, `display_name`,
`is_active` plus the standard id/timestamp mixins; a lookup table for
supported platforms, not a scraped-content entity). Grepping the whole
`app/` tree (outside `app/repositories/` itself) turns up **no call site at
all** for either `PlatformRepository` or `get_by_name` — it is referenced
only in `app/repositories/__init__.py` (re-export), `docs/architecture.md:64`
(mentioned as part of the repository layer list), and
`tests/unit/test_repositories_extended.py:53,805,812` (direct unit tests of
`create`/`get_by_name`). No service, the ingestion pipeline, the retrieval
layer, or the AI assistant currently reads or writes the `platforms` table —
platform identity elsewhere in the app is carried entirely by the
`PlatformName` string enum on each row (`author.platform`, `post.platform`,
etc.), not by a foreign key into this table.

---

## `app/repositories/post_repository.py`

```python
from __future__ import annotations
import asyncio
from datetime import datetime
from typing import Any
from app.models.pydantic import Post
from app.repositories.base import BaseRepository

class PostRepository(BaseRepository[Post]):
    table_name = "posts"
    model = Post

    async def get_by_platform_post_id(self, platform: str, platform_post_id: str) -> Post | None:
        ...  # not called outside tests
```

`Post` (`app/models/pydantic/post.py`) is the central content entity;
`dedup_key` is `f"{platform}:{platform_post_id}"`.

```python
    async def upsert_post(self, post: Post) -> Post:
        return await self.upsert(post, on_conflict="platform,platform_post_id")

    async def bulk_upsert_posts(self, posts: list[Post]) -> list[Post]:
        return await self.bulk_upsert(posts, on_conflict="platform,platform_post_id")
```

`bulk_upsert_posts` is the core production write path: called from
`IngestionPipeline._run` (`app/ingestion/pipeline.py:161-170`) after posts
have had their `author_id` remapped via `author_id_map`; its response
(`persisted_posts`) is used to build `post_id_map`, which every downstream
entity in the same run (`videos`, `comments`, `media`, `hashtags`,
`mentions`, `engagement`, embeddings) uses to resolve its own client-side
`post_id` reference to the real one. `upsert_post` (singular) is not called
anywhere in `app/` outside tests.

```python
    async def by_platform(self, platform: str, *, limit: int = 100, offset: int = 0) -> list[Post]:
        return await self.list_all(
            filters={"platform": platform}, order_by="posted_at", descending=True,
            limit=limit, offset=offset,
        )
```

Not called anywhere in `app/` outside tests (`AnalyticsService.platform_distribution`
uses the inherited `count(filters=...)` instead, not this method, since it
only needs a row count per platform, not the rows themselves).

```python
    async def by_author(self, author_id: str, *, limit: int = 100) -> list[Post]:
        return await self.list_all(
            filters={"author_id": author_id}, order_by="posted_at", descending=True, limit=limit
        )
```

Not called anywhere in `app/` outside tests.

```python
    async def posted_between(
        self, start: datetime, end: datetime, *, platform: str | None = None, limit: int = 200
    ) -> list[Post]:
        def _run() -> Any:
            query = (
                self._table.select("*").is_("deleted_at", "null")
                .gte("posted_at", start.isoformat()).lte("posted_at", end.isoformat())
            )
            if platform:
                query = query.eq("platform", platform)
            return query.order("posted_at", desc=True).limit(limit).execute()
        response = await asyncio.to_thread(_run)
        return [self._deserialize(row) for row in response.data]
```

Hand-rolled (not `list_all`) because `list_all`'s filter dict is
equality-only; a date-range query needs `.gte()`/`.lte()`. Not called
anywhere in `app/` outside tests — a "posts in this window" query awaiting a
production caller (e.g. a future trend-analysis dashboard feature).

### Where `PostRepository` is used elsewhere

- `app/ingestion/pipeline.py:36,95,108,161-170` — `bulk_upsert_posts`.
- `app/services/analytics_service.py:20,28,36,44-45,50-54` — `count` (inherited).
- `app/retrieval/service.py:20,38,44,180,190,223` — constructed in
  `RetrievalService.__init__`; `get_by_id` (inherited) is used in both
  `popular_posts` and `_apply_filters` to resolve a candidate result's post
  row.
- `tests/unit/test_repositories.py`, `tests/unit/test_repositories_extended.py`,
  `tests/integration/test_ingestion_pipeline.py`.

---

## `app/repositories/query_log_repository.py`

Two repositories in one file, both append-only logging tables used by the AI
assistant — kept together because they're a matched pair ("what was asked" /
"what the assistant did about it") that are always written in the same
`Assistant.ask` turn.

```python
from __future__ import annotations
from app.models.pydantic import AssistantLog, QueryLog
from app.repositories.base import BaseRepository

class QueryLogRepository(BaseRepository[QueryLog]):
    table_name = "query_logs"
    model = QueryLog

    async def by_conversation(self, conversation_id: str, *, limit: int = 100) -> list[QueryLog]:
        return await self.list_all(
            filters={"conversation_id": conversation_id}, limit=limit, include_deleted=True
        )

    async def recent(self, *, limit: int = 50) -> list[QueryLog]:
        return await self.list_all(order_by="created_at", descending=True, limit=limit, include_deleted=True)
```

`QueryLog` (`app/models/pydantic/conversation.py`) has no `SoftDeleteMixin` —
`include_deleted=True` on both methods is again a documentation-of-intent
no-op given `list_all`'s field-presence guard. `by_conversation` is not
called anywhere in `app/` outside tests. `recent` is called by
`AnalyticsService.ai_query_stats` (`app/services/analytics_service.py:68-70`)
to compute average query latency for the dashboard.

`QueryLog` has no upsert method — every query is logged as a genuinely new
row via the inherited `create`, called directly by `Assistant.ask`
(`app/ai/assistant.py:206-214`).

```python
class AssistantLogRepository(BaseRepository[AssistantLog]):
    table_name = "assistant_logs"
    model = AssistantLog

    async def by_conversation(self, conversation_id: str, *, limit: int = 100) -> list[AssistantLog]:
        return await self.list_all(filters={"conversation_id": conversation_id}, limit=limit, include_deleted=True)
```

Not called anywhere in `app/` outside tests.

```python
    async def failures(self, *, limit: int = 50) -> list[AssistantLog]:
        def _has_error(log: AssistantLog) -> bool:
            return bool(log.error)
        logs = await self.list_all(order_by="created_at", descending=True, limit=limit, include_deleted=True)
        return [log for log in logs if _has_error(log)]
```

Filters for failures **in Python after fetching**, not via a DB-level `WHERE
error IS NOT NULL` — simplest correct approach given `list_all`'s
equality-only filter dict has no "is not null" predicate, and this method has
no current production caller so the extra round-trip cost of over-fetching
`limit` rows and discarding successes is untested against real volume. Not
called anywhere in `app/` outside tests — a diagnostic query with no wired-up
dashboard widget yet.

`AssistantLog` likewise has no upsert method — every assistant turn's log is
a new row via the inherited `create`, called by `Assistant.ask`
(`app/ai/assistant.py:215-228`), immediately after the `QueryLog` write in
the same turn.

### Where `QueryLogRepository`/`AssistantLogRepository` are used elsewhere

- `app/services/analytics_service.py:21,34,42,68-70` — `QueryLogRepository.recent`.
- `app/ai/assistant.py:29,92-93,102-103,206-228` — both constructed in
  `Assistant.__init__`; both used via inherited `create` for logging every
  turn.
- `tests/unit/test_repositories_extended.py`.

---

## `app/repositories/scrape_job_repository.py`

```python
"""Repository for `scrape_jobs` — tracks each ingestion pipeline run so
progress can be reported and resumed (see app/ingestion/pipeline.py).
"""
from __future__ import annotations
from datetime import UTC, datetime
from pydantic import Field
from app.models.pydantic.base import BaseSchema, IdentifiedMixin
from app.models.pydantic.enums import PlatformName, ScrapeJobStatus
from app.repositories.base import BaseRepository
```

Like `embedding_repository.py`, this file defines its own Pydantic model
rather than importing one from `app/models/pydantic/`, because `ScrapeJob`
isn't a cross-platform *content* entity — it's operational metadata about a
pipeline run, scoped entirely to the repository that owns its table.

```python
class ScrapeJob(IdentifiedMixin, BaseSchema):
    """... unlike most tables in this schema, has no `updated_at` column
    (a job's lifecycle is tracked via `started_at`/`finished_at`/`status`
    instead), so this does not use `TimestampMixin`."""

    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    platform: PlatformName
    job_type: str
    status: ScrapeJobStatus = ScrapeJobStatus.PENDING
    target: str | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    records_scraped: int = 0
    error: str | None = None
```

`created_at` is declared manually here (rather than via `CreatedAtMixin`)
purely because the class only inherits `IdentifiedMixin, BaseSchema` — same
field, same default factory, just spelled out instead of mixed in (there's no
functional difference from using `CreatedAtMixin`, only a slightly different
inheritance choice than every other append-only-log model in the app, all of
which do use `CreatedAtMixin`). `status` defaults to `ScrapeJobStatus.PENDING`
— every job starts pending even though, per `start()` below, it's immediately
constructed with `status=RUNNING`, so the class-level default is really only
meaningful if a `ScrapeJob` is ever constructed some other way (e.g. tests).

```python
class ScrapeJobRepository(BaseRepository[ScrapeJob]):
    table_name = "scrape_jobs"
    model = ScrapeJob

    async def start(self, platform: str, job_type: str, target: str | None = None) -> ScrapeJob:
        job = ScrapeJob(
            platform=PlatformName(platform), job_type=job_type,
            status=ScrapeJobStatus.RUNNING, target=target, started_at=datetime.now(UTC),
        )
        return await self.create(job)
```

Called at the very top of `IngestionPipeline.ingest`
(`app/ingestion/pipeline.py:126`) — every ingestion run begins by creating a
`RUNNING` job row before anything else happens, so a run's progress/existence
can always be inspected even if it later fails outright.

```python
    async def mark_succeeded(self, job_id: str, records_scraped: int) -> ScrapeJob:
        return await self.update(job_id, {
            "status": ScrapeJobStatus.SUCCEEDED.value, "finished_at": datetime.now(UTC).isoformat(),
            "records_scraped": records_scraped,
        })

    async def mark_partial(self, job_id: str, records_scraped: int, error: str) -> ScrapeJob:
        return await self.update(job_id, {
            "status": ScrapeJobStatus.PARTIAL.value, "finished_at": datetime.now(UTC).isoformat(),
            "records_scraped": records_scraped, "error": error,
        })

    async def mark_failed(self, job_id: str, error: str) -> ScrapeJob:
        return await self.update(job_id, {
            "status": ScrapeJobStatus.FAILED.value, "finished_at": datetime.now(UTC).isoformat(),
            "error": error,
        })
```

Three exclusive terminal-state transitions, each explicitly passing
`.value` for the enum (needed because `update()`'s `data: dict[str, Any]` is
sent to PostgREST as-is, unlike `_serialize`, which handles enum→value
conversion automatically via `model_dump(mode="json")` — here there's no
Pydantic serialization step in between, so the enum member itself would
otherwise be sent, which PostgREST/`postgrest-py`'s JSON encoder can't
handle for a plain `StrEnum` passed loose in a dict). All three are called
from `IngestionPipeline.ingest` (`app/ingestion/pipeline.py:126-140`): success
if `report.errors` is empty, `mark_partial` if some sub-steps failed but the
run completed, `mark_failed` (in the `except Exception` handler) if the run
raised before finishing at all.

```python
    async def recent(self, *, platform: str | None = None, limit: int = 50) -> list[ScrapeJob]:
        filters = {"platform": platform} if platform else None
        return await self.list_all(
            filters=filters, order_by="created_at", descending=True, limit=limit, include_deleted=True
        )
```

`include_deleted=True` — again a no-op given `ScrapeJob` has no
`deleted_at`/`SoftDeleteMixin`. Called by `AnalyticsService.recent_scrape_jobs`
(`app/services/analytics_service.py:65-66`), feeding the dashboard's "recent
scrape jobs" widget.

### Where `ScrapeJobRepository` is used elsewhere

- `app/ingestion/pipeline.py:37,102,115,126-140` — `start`, `mark_succeeded`,
  `mark_partial`, `mark_failed`.
- `app/services/analytics_service.py:22,33,41,65-66` — `recent`.
- `tests/unit/test_repositories_extended.py`, `tests/integration/test_ingestion_pipeline.py`.

---

## Shared patterns across every repository

1. **One repository per table, always `BaseRepository[ModelT]`.** Every
   concrete repository sets exactly two class attributes (`table_name`,
   `model`) and adds only the query shapes its table actually needs. No
   repository overrides `_serialize`, `_serialize_for_upsert`, or
   `_deserialize` — the base implementation is universal across every table
   in the schema.

2. **`_serialize_for_upsert` / the id-remapping dance.** Every model uses
   `IdentifiedMixin`'s client-generated `uuid.uuid4()` default. Because
   `_serialize_for_upsert` drops `id` from the payload, an `upsert`/
   `bulk_upsert` call's **response** is the only trustworthy source of a
   record's real, DB-persisted `id` — never the object that was passed in.
   Every multi-entity write path follows the same three-step shape:
   `dedupe → bulk_upsert(objs, on_conflict=...) → remap child FKs from the
   response`. Concretely:
   - `app/ingestion/pipeline.py::_build_id_map` builds `{local_id:
     persisted_id}` by matching on each model's `dedup_key` (not list
     position — Postgres bulk upsert responses aren't guaranteed to preserve
     input order), and `IngestionPipeline._run` chains this through authors →
     channels → posts → videos → comments, each stage's map feeding the
     next's FK remapping via `.model_copy(update={...})`.
   - `app/embeddings/service.py::EmbeddingService.embed_batch` does the same
     thing one level down: re-keys by the **persisted** `Document` rows
     (matched on `(source_type, source_id)`, not the locally-constructed
     ones) before building `EmbeddingRow.document_id` references.

3. **`on_conflict` mirrors a real DB unique constraint, always supplied by
   the caller of `upsert`/`bulk_upsert`, never inferred.** Every
   content-entity table dedups on a natural key: `"platform,platform_user_id"`
   (authors), `"platform,platform_channel_id"` (channels),
   `"platform,platform_video_id"` (videos), `"platform,platform_post_id"`
   (posts), `"platform,platform_comment_id"` (comments), `"tag"` (hashtags),
   `"post_id,hashtag_id"` (post_hashtags), `"post_id"` (engagement),
   `"source_type,source_id"` (documents), `"source_id,source_type,model"`
   (embeddings). Tables with **no** natural key beyond their own generated id
   (`conversations`, `messages`, `query_logs`, `assistant_logs`,
   `scrape_jobs`) have no `upsert`/`bulk_upsert` method at all — only
   `create`, because every row genuinely is new.

4. **Two different dedup strategies for child rows without a DB unique
   constraint.** `Media` and `Mention` have no natural key at the database
   level (a post could legitimately have visually-identical media if the
   schema allowed a constraint, but URL identity is what's actually used as
   the practical key) — instead of `upsert`, both repositories expose a
   `by_post` read, and the ingestion pipeline does the fetch-existing →
   diff-by-identity (URL for media, username for mentions) → `insert`-only-
   the-new-ones dance itself (`app/ingestion/pipeline.py::_ingest_media`,
   `::_ingest_mentions`).

5. **Bulk over per-row, everywhere the pipeline writes.** Every
   `bulk_upsert_*`/`bulk_create_*` method short-circuits on an empty list and
   otherwise sends the whole batch as one `postgrest` request — this is
   explicitly why `BaseRepository.bulk_upsert`'s docstring says "a scrape of
   thousands of posts is a handful of round trips, not one-per-row," and
   it's the reason `IngestionPipeline._run` and `EmbeddingService.embed_batch`
   are both structured as "collect everything for this stage, then one bulk
   call," rather than looping with per-item awaits.

6. **`list_all`'s soft-delete guard is schema-driven, not per-repository
   configuration.** `"deleted_at" in self.model.model_fields` means the base
   class automatically applies (or skips) the `deleted_at IS NULL` filter
   correctly for both `SoftDeleteMixin` models (`Author`, `Channel`, `Video`,
   `Post`, `Comment`, `Conversation`) and non-soft-deletable models
   (`Engagement`, `Hashtag`, `Mention`, `Media`, `Document`, `ChatMessage`,
   `QueryLog`, `AssistantLog`, `ScrapeJob`, `Platform`) without any repository
   having to declare which kind it is. Several call sites still pass
   `include_deleted=True` explicitly on non-soft-deletable models
   (`EngagementRepository.top_by_likes`, `MessageRepository.by_conversation`,
   both `QueryLogRepository`/`AssistantLogRepository` methods,
   `ScrapeJobRepository.recent`) — functionally redundant given the guard,
   but documents at each call site that "this table is never soft-deleted"
   rather than leaving the reader to check the model definition.

7. **A visible split between "wired into production" and "test-only"
   convenience methods.** Grepping the whole `app/` tree (outside
   `app/repositories/`) shows a consistent pattern: every `bulk_upsert_*`/
   `bulk_create_*`/`upsert_for_post` method that the ingestion pipeline needs
   *is* called from `app/ingestion/pipeline.py`, and every read method a
   service/retrieval/assistant layer needs *is* called from exactly one of
   `app/services/analytics_service.py`, `app/services/chat_service.py`,
   `app/ai/assistant.py`, or `app/retrieval/service.py`. But a sizeable
   minority of single-row convenience lookups and list queries — every
   `get_by_platform_*_id` method, `ChannelRepository.by_author`,
   `VideoRepository.by_channel`, `CommentRepository.by_post`/`replies_to`,
   `ConversationRepository.by_user`/`archive`, `MentionRepository.by_username`,
   `PostRepository.by_platform`/`by_author`/`posted_between`,
   `QueryLogRepository.by_conversation`, `AssistantLogRepository.by_conversation`/
   `failures`, and the entirety of `PlatformRepository` — exist, are correct,
   and are exercised by `tests/unit/test_repositories.py`/
   `test_repositories_extended.py`, but currently have **no caller anywhere
   in application code**. They read as either forward-looking API surface
   (symmetry with sibling repositories, anticipated dashboard/CLI features)
   or dead code, depending on how strictly the project wants to keep the
   repository layer trimmed to only what's actually driven today.
