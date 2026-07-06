# `app/models/pydantic/embedding.py` — line-by-line and how it fits the app

This file defines **one Pydantic model**, `EmbeddingDocument`. This document explains
every piece of it, then traces how (and where) embeddings actually flow through the
app at runtime — because the surprising part of this file is that `EmbeddingDocument`
itself is **not** the class used by the live pipeline. Read to the end for why.

---

## 1. The file, piece by piece

```python
from __future__ import annotations
```
Postpones evaluation of type annotations (PEP 563). It lets the class use its own
name (`EmbeddingDocument`) as a return-type annotation on a method defined *inside*
that same class (see `_validate_vector_length` below) without a `NameError`, since
the class doesn't exist yet at the point Python parses the method signature.

```python
from pydantic import Field, model_validator
```
- `Field(...)` — lets you attach metadata (defaults, descriptions, factory functions)
  to a model attribute beyond a bare type annotation.
- `model_validator` — a Pydantic v2 decorator for validation logic that needs to see
  *multiple* fields at once (as opposed to `field_validator`, which only sees one
  field in isolation). Used here because "is the vector length valid" depends on
  comparing two different fields (`vector` and `dimensions`).

```python
from app.models.pydantic.base import BaseSchema, IdentifiedMixin, TimestampMixin
from app.models.pydantic.enums import EmbeddingSourceType, PlatformName
```
Shared building blocks used by *every* domain model in the app, not just this one:

| Import | Defined in | What it contributes |
|---|---|---|
| `BaseSchema` | `app/models/pydantic/base.py` | Base `pydantic.BaseModel` config: `populate_by_name=True` (accept alias or field name), `str_strip_whitespace=True` (auto `.strip()` all strings), `extra="ignore"` (drop unknown JSON keys instead of erroring), `use_enum_values=True` (store enums as their plain `str` value, not the enum member) |
| `IdentifiedMixin` | same | Adds `id: uuid.UUID` with `default_factory=uuid.uuid4` — a client-generated primary key so a record can be referenced (e.g. linked to an embedding) *before* it's ever written to the DB |
| `TimestampMixin` | same | Adds `created_at` **and** `updated_at`, both `datetime`, both defaulted to `datetime.now(UTC)` — for tables that have an `updated_at` trigger (see `migrations/0002-0003`) |
| `EmbeddingSourceType` | `app/models/pydantic/enums.py` | `StrEnum`: `POST`, `COMMENT`, `CAPTION`, `DESCRIPTION`, `TRANSCRIPT` — what *kind* of record this embedding was derived from |
| `PlatformName` | same | `StrEnum`: `instagram`, `twitter`, `youtube`, `reddit`, `linkedin`, `facebook`, `tiktok`, `news` |

### The class

```python
class EmbeddingDocument(IdentifiedMixin, TimestampMixin, BaseSchema):
```
Multiple inheritance — Pydantic merges the fields of all three parents. Net effect:
`EmbeddingDocument` gets `id`, `created_at`, `updated_at` for free, plus the config
from `BaseSchema` (strip whitespace, ignore unknown JSON keys, etc.), on top of the
fields declared below.

```python
    source_type: EmbeddingSourceType
```
Required field. Which table/entity this embedding's text came from — a `Post`
caption, a `Comment` body, a video `CAPTION`/`DESCRIPTION`, or a `TRANSCRIPT`.

```python
    source_id: str = Field(..., description="ID of the Post/Comment/Video this was derived from")
```
Required (the `...` is Pydantic's "no default, must be supplied" sentinel). The
foreign key back to whichever row was embedded — stored as `str` rather than
`uuid.UUID` because it's a loose reference across several possible tables, not a
DB-enforced FK to one specific table.

```python
    platform: PlatformName
```
Required. Which platform the source record belongs to — lets semantic search be
scoped/filtered by platform without a join back to `posts`.

```python
    text: str
```
Required. The exact text that was embedded — kept alongside the vector so you can
inspect/debug/re-derive without re-fetching the source row.

```python
    vector: list[float] = Field(default_factory=list, repr=False)
```
The embedding itself — a list of floats (e.g. 1536 numbers for
`text-embedding-3-small`). Two things worth noting:
- `default_factory=list` means it defaults to `[]`, not required — this is what
  allows constructing/validating a document *before* the embedding API call has
  happened (see the "empty vector" validator behavior below).
- `repr=False` excludes it from `__repr__`/`print()` output — a 1536-float list
  would otherwise flood the terminal/log every time a document prints.

```python
    model: str
```
Required. Name of the embedding model that produced `vector` (e.g.
`"text-embedding-3-small"`). Stored per-row (not globally) because different rows
can be embedded by different model versions over time, and dimensions/vectors from
different models aren't comparable.

```python
    dimensions: int
```
Required. Expected length of `vector` for this `model`. Used by the validator below
to catch a mismatched/corrupt vector early, and used by the actual DB schema to size
the `pgvector` column.

```python
    checksum: str = Field(..., description="sha256 of `text`, used to avoid re-embedding")
```
Required. A SHA-256 hex digest of `text`. This is the cache key that lets the
pipeline skip calling the embedding API again for content that hasn't changed since
the last run — expensive (paid, rate-limited) API calls are the whole reason this
field exists.

```python
    metadata: dict = Field(default_factory=dict)
```
Free-form JSON bag for anything not worth a first-class column (e.g. extra context
passed through from the source record).

```python
    @model_validator(mode="after")
    def _validate_vector_length(self) -> EmbeddingDocument:
        if self.vector and len(self.vector) != self.dimensions:
            raise ValueError(f"vector has {len(self.vector)} dims but dimensions={self.dimensions}")
        return self
```
- `mode="after"` — runs *after* all individual fields have already been
  type-validated/coerced, and receives the fully-built model instance (`self`), so it
  can compare multiple fields together.
- Logic: `if self.vector and ...` — the check is skipped entirely when `vector` is
  `[]` (falsy). This deliberately allows constructing an `EmbeddingDocument` *before*
  the vector has been computed (e.g. as a placeholder / staging step), while still
  catching corruption once a vector *is* present.
- Returns `self` — required by Pydantic's `"after"` validator contract: the
  (possibly mutated) instance must be returned.
- Raises `ValueError`, which Pydantic wraps into its own `ValidationError` at
  construction/`model_validate` time.

---

## 2. Where is `EmbeddingDocument` actually used?

This is the important, slightly counter-intuitive part. Searching the whole `app/`
tree and `tests/` for `EmbeddingDocument` turns up only:

- `app/models/pydantic/embedding.py` — the definition itself
- `app/models/pydantic/__init__.py` — re-exported for convenience (`from app.models.pydantic import EmbeddingDocument`)
- `tests/unit/test_models.py` — direct unit tests of the validator (valid length,
  mismatched length raises, empty vector always allowed, round-trip via
  `model_dump()` / `model_validate()`)
- `docs/architecture.md` and `specs.md` — mentioned as one of the required domain
  models per the project spec

**It is never instantiated by the ingestion pipeline, the embedding service, or the
retrieval service.** Those all use two *different*, DB-shaped models defined in
`app/repositories/embedding_repository.py`:

- `Document` — mirrors the `documents` table (source-of-truth text + `search_vector` for keyword search)
- `EmbeddingRow` — mirrors the `embeddings` table (the vector row)

Why two extra classes instead of reusing `EmbeddingDocument`? Two real, structural
reasons:

1. **The DB schema splits one concept into two tables.** `documents` (human-readable
   text, full-text search) and `embeddings` (vector, semantic search) are separate
   tables with separate write patterns — see the docstring at the top of
   `embedding_repository.py`. `EmbeddingDocument` models them as *one* combined
   concept (text + vector together), which matches the spec's model list
   (`specs.md`) but not the actual two-table schema that got built.
2. **`EmbeddingRow` needs a pgvector-specific parsing step that `EmbeddingDocument`
   doesn't have** — `EmbeddingRow._parse_pgvector_string` (a `field_validator`) is
   required because PostgREST returns the `vector` column back as a Postgres
   array-literal *string* (`"[0.01,-0.02,...]"`), not a JSON array, so a row read
   back after insert/upsert needs that string parsed into `list[float]` before
   Pydantic's `list[float]` type validation can succeed. `EmbeddingDocument` has no
   equivalent, so it can't round-trip data read back from the `embeddings` table
   as-is.

So in practice, `EmbeddingDocument` functions as **living documentation of the
target domain shape** (satisfies the spec's model list, gives you one place with a
clean docstring + validator to look at) — while the real runtime path uses the
DB-table-shaped `Document`/`EmbeddingRow` pair. If you're trying to understand "how
does an embedding actually get created and stored," follow the trail in section 3,
not `EmbeddingDocument`.

---

## 3. The real runtime flow (what actually happens)

```
app/ingestion/pipeline.py  (_generate_embeddings)
        |
        v  builds EmbeddableItem(s)
app/embeddings/service.py  (EmbeddingService.embed_batch)
        |
        |-- checksum check (skip unchanged) --> app/repositories/embedding_repository.py (EmbeddingRepository.get_by_checksum)
        |-- calls provider.embed_texts()     --> app/embeddings/providers.py (OpenAIEmbeddingProvider)
        |-- builds Document rows             --> DocumentRepository.bulk_upsert  (writes `documents` table)
        v-- builds EmbeddingRow rows         --> EmbeddingRepository.bulk_upsert_embeddings (writes `embeddings` table)

app/retrieval/service.py  (RetrievalService.semantic_search)
        |
        v  embeds the *query* text, then calls EmbeddingRepository.match()
        --> Postgres RPC `match_embeddings` (cosine similarity, pgvector) --> RetrievalResult list
```

### 3a. `app/embeddings/service.py`

- **`checksum_of(text: str) -> str`** — `hashlib.sha256(text.encode()).hexdigest()`.
  Free function (not a method) because it's pure and reused for both computing a new
  checksum and (implicitly) comparing against a stored one.

- **`EmbeddableItem`** (`@dataclass(slots=True, frozen=True)`) — a plain dataclass,
  *not* a Pydantic model, representing "one unit of text to embed, tied back to its
  source record": `source_type`, `source_id`, `platform`, `text`, optional
  `metadata`. `frozen=True` + `slots=True` — immutable, memory-lean, since these are
  created in bulk (one per post/comment/video transcript) and never mutated.
  Constructed in `app/ingestion/pipeline.py::_generate_embeddings`.

- **`EmbeddingService`** — the class that actually orchestrates embedding + storage.
  - `__init__(provider=None, document_repo=None, embedding_repo=None)` — all three
    dependencies are optional and default-constructed if omitted (`OpenAIEmbeddingProvider()`,
    `DocumentRepository()`, `EmbeddingRepository()`). This is dependency injection for
    testability — tests can pass fakes/mocks instead.
  - **`embed_batch(items: list[EmbeddableItem]) -> int`** — the main entry point.
    1. For each item: strip text, skip if blank; compute checksum; look up any
       existing embedding row for `(source_id, source_type, model)` via
       `embedding_repo.get_by_checksum`; if the checksum matches, **skip** (no API
       call) — this is the "avoid re-embedding unchanged content" optimization the
       `checksum` field exists for.
    2. Batch-call `provider.embed_texts(...)` once for all *pending* (changed) items
       — one network round-trip instead of one-per-item.
    3. Build `Document` rows and `bulk_upsert` them into `documents` (on conflict on
       `source_type, source_id`).
    4. Re-key by the **persisted** documents' ids (not the locally-generated ones —
       see the inline comment about `BaseRepository._serialize_for_upsert`: on an
       upsert of an *existing* row, the DB keeps its original `id`, which won't match
       a freshly `uuid.uuid4()`-generated local one).
    5. Build `EmbeddingRow` objects referencing `document_id`, and
       `bulk_upsert_embeddings` them into `embeddings`.
    6. Returns the count of embeddings actually (re)computed.
  - **`embed_one(item)`** — convenience wrapper, `embed_batch([item])`.

  Note the comment in the code: `Document.source_type` reads back as a plain `str`
  (because `BaseSchema` sets `use_enum_values=True`), while `EmbeddableItem.source_type`
  stays a real `EmbeddingSourceType` enum (it's a dataclass, not a `BaseSchema`
  subclass) — hence the explicit `.value` access when matching the two up.

### 3b. `app/repositories/embedding_repository.py`

- **`Document(IdentifiedMixin, CreatedAtMixin, BaseSchema)`** — mirrors the
  `documents` table: `source_type`, `source_id`, `platform`, `content`, `metadata`.
  No `updated_at` (uses `CreatedAtMixin`, not `TimestampMixin`) because that table
  has no `updated_at` trigger.
- **`EmbeddingRow(BaseModel)`** — mirrors the `embeddings` table: `document_id`
  (FK to `Document.id`), `source_type`, `source_id`, `platform`, `model`,
  `dimensions`, `checksum`, `vector`, `metadata`. Includes
  `_parse_pgvector_string`, the `field_validator` described in section 2 that
  converts Postgres's `"[0.01,-0.02,...]"` string form back into `list[float]`.
- **`DocumentRepository(BaseRepository[Document])`** — `get_by_source`,
  `upsert_document`.
- **`EmbeddingRepository(BaseRepository[EmbeddingRow])`**:
  - `get_by_checksum(source_id, source_type, model)` — used by
    `EmbeddingService.embed_batch` to decide whether to skip re-embedding.
  - `upsert_embedding` / `bulk_upsert_embeddings` — write path.
  - `match(query_vector, match_count=10, platform=None)` — calls the
    `match_embeddings` Postgres RPC (cosine distance computed **in the database**,
    so the full vector table is never pulled over the wire). This is what
    `RetrievalService.semantic_search` calls.

### 3c. `app/ingestion/pipeline.py::_generate_embeddings`

Called at the end of every ingestion run (`IngestionPipeline._run`), after posts /
comments / videos have already been persisted. For each **persisted** post, comment,
and video, it builds an `EmbeddableItem` from whatever text field is relevant
(`post.caption or post.content`, `comment.content`, `video.transcript`), skips blank
text, and hands the whole batch to `EmbeddingService.embed_batch`. Any embedding
failure is caught and appended to `IngestionReport.errors` rather than aborting the
pipeline — embedding is treated as best-effort, not required for a scrape to
"succeed."

### 3d. `app/embeddings/providers.py`

- **`EmbeddingProvider`** — a `Protocol` (structural typing, not an ABC) requiring
  `model_name: str`, `dimensions: int`, and `async embed_texts(texts) -> list[list[float]]`.
  Using a `Protocol` instead of inheritance means swapping in a different provider
  (e.g. a local sentence-transformers model) never requires touching
  `EmbeddingService` — any object with that shape satisfies the type.
- **`OpenAIEmbeddingProvider`** — the concrete default, wraps `AsyncOpenAI`. Reads
  `model_name`/`dimensions` from app settings if not passed explicitly.
  `embed_texts` is wrapped in `@with_retry(exceptions=(Exception,), max_attempts=3)`
  and translates any OpenAI SDK exception into the app's own `EmbeddingError`.

### 3e. `app/retrieval/service.py`

- **`semantic_search(query, platform=None, limit=20)`** — embeds the *query string*
  itself (one-item call to the same provider), then calls
  `embedding_repo.match(...)` to get the nearest rows by cosine similarity, and wraps
  each into a `RetrievalResult` with `score = similarity`.
- **`hybrid_search(...)`** — runs `keyword_search` (Postgres full-text on
  `documents.search_vector`) and `semantic_search` concurrently (`asyncio.gather`),
  merges results keyed by `(source_type, source_id)`, and combines scores with fixed
  weights `_KEYWORD_WEIGHT = 0.4` / `_SEMANTIC_WEIGHT = 0.6` (semantic weighted
  higher because it degrades more gracefully on paraphrased queries).

---

## 4. Summary — what to actually remember

- `EmbeddingDocument` (this file) is a clean, spec-matching, **validated schema
  definition** used today only for its own unit tests and as a documented "shape"
  of an embedding. It is not wired into ingestion, storage, or retrieval.
- The classes that are actually written to and read from Supabase are `Document`
  and `EmbeddingRow` in `app/repositories/embedding_repository.py`.
- The end-to-end real flow is: `IngestionPipeline._generate_embeddings` →
  `EmbeddableItem` → `EmbeddingService.embed_batch` → `OpenAIEmbeddingProvider.embed_texts`
  → `DocumentRepository`/`EmbeddingRepository` upserts → later read back by
  `RetrievalService.semantic_search`/`hybrid_search` via the `match_embeddings` RPC.
- The `checksum` field (present in both `EmbeddingDocument` and `EmbeddingRow`) is
  the mechanism that avoids paying for re-embedding text that hasn't changed.
