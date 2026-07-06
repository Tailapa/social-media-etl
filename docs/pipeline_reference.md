# Ingestion, Embeddings & Retrieval pipeline — file-by-file reference

Covers `app/ingestion/pipeline.py`, `app/embeddings/service.py`, `app/embeddings/providers.py`,
`app/retrieval/service.py`, and `app/retrieval/models.py` — the code that takes a scraper's
output and turns it into persisted rows, vectors, and finally search results. This is the
"backbone" module group: almost everything else in the app (Gradio UI, AI assistant, CLI
scripts) calls into this layer rather than touching repositories directly.

For the embedding *domain model* (`EmbeddingDocument`) see `docs/embedding_model_explained.md`
— this document focuses on the services that actually run at request/ingestion time.

---

## `app/ingestion/pipeline.py`

**Purpose** (from the module docstring): orchestrates the full flow
`Apify -> Raw JSON -> Validation -> Pydantic -> Normalization -> Deduplication -> Supabase ->
Embedding generation -> Vector storage`. Scrapers (`app/apify/*`) already hand back validated,
normalized Pydantic models wrapped in a `ScrapeResult`; this module owns everything
*downstream* of that: batch dedup, persisting through repositories with correct foreign-key
remapping, building derived rows (engagement/hashtags/mentions/media), and triggering
embeddings. Every step isolates failures (log + continue) instead of aborting the whole run,
and a `ScrapeJob` row is updated throughout so a run's progress/outcome can always be inspected
later (e.g. from the Gradio UI or a status query).

### Imports and why they matter
- `ScrapeResult` (`app.apify.base.scraper`) — the container scrapers return: lists of
  authors/channels/posts/videos/comments already validated as Pydantic models.
- `EmbeddableItem`, `EmbeddingService` (`app.embeddings.service`) — used only in
  `_generate_embeddings` at the very end of a run.
- `Hashtag`, `Mention`, `Post`, `PostHashtag` (`app.models.pydantic`) — domain models
  constructed fresh inside this pipeline (hashtags/mentions aren't produced by the scraper
  layer itself, they're derived from `Post.hashtags` / `Post.mentions` lists).
- `EmbeddingSourceType` (`app.models.pydantic.enums`) — tags each embeddable chunk as
  `POST`, `COMMENT`, or `TRANSCRIPT`.
- `NORMALIZERS`, `dedupe_by_key` (`app.normalization`) — `NORMALIZERS` is a
  `{platform: normalizer}` registry used in `_ingest_engagement` to pull the
  platform-specific `extract_engagement` function; `dedupe_by_key` collapses duplicate
  records within one batch (e.g. the same post scraped twice in one run) before upserting.
- One repository class per entity (`AuthorRepository`, `ChannelRepository`, `VideoRepository`,
  `CommentRepository`, `EngagementRepository`, `HashtagRepository`, `PostHashtagRepository`,
  `MediaRepository`, `MentionRepository`, `PostRepository`, `ScrapeJobRepository`) — see
  `docs/repositories_reference.md` for each one's own internals.

### `ModelT = TypeVar("ModelT", bound=BaseModel)`
A generic type variable so `_safe_bulk` (below) can be typed as "takes a list of some Pydantic
model type, returns a list of the same type" without hardcoding which model.

### `IngestionReport` (`@dataclass(slots=True)`)
Summary of one pipeline run, returned to callers (CLI scripts, the Gradio "run a scrape"
action) for progress reporting.

| Field | Type | Meaning |
|---|---|---|
| `job_id` | `str \| None` | The `ScrapeJob` row's id, set once `ingest()` starts one |
| `authors_upserted` … `engagement_upserted` | `int` | Running counters, one per entity type, incremented as each `_ingest_*` step completes |
| `embeddings_generated` | `int` | Count returned by `EmbeddingService.embed_batch` |
| `errors` | `list[str]` | Human-readable error strings appended by every `_safe_bulk`/`_ingest_*`/`_generate_embeddings` call that catches an exception — the pipeline never raises for a single-entity-type failure |

- **`total_records` (property, `-> int`)** — sum of the five "core" upserted counts (authors,
  channels, posts, videos, comments). Used by `ingest()` to decide `mark_succeeded` vs
  `mark_partial` record counts; deliberately excludes media/hashtags/mentions/engagement/
  embeddings since those are derived, not primary content.

### `_build_id_map(local_items, persisted_items) -> dict[str, str]`
Maps each locally-generated (client-side `uuid.uuid4()`) `.id` to the id Postgres actually
persisted, matched by each model's `dedup_key` (a stable business key, e.g. platform + native
post id — defined on the Pydantic models themselves, not in this file) rather than by list
position. This exists because **a bulk upsert's response order is not guaranteed to match the
input order**, so zipping `local_items` with `persisted_items` by index would silently
mis-link foreign keys. Returns only entries whose `dedup_key` was actually found among the
persisted rows (an item that failed to persist is simply absent from the map).

### `IngestionPipeline` class

**`__init__(self, *, author_repo=None, channel_repo=None, ..., embedding_service=None)`**
Every dependency is optional and defaults to a freshly constructed instance
(`AuthorRepository()`, ..., `EmbeddingService()`). This is dependency injection purely for
testability — unit tests can substitute fakes/mocks for any repository or the embedding
service without changing pipeline logic. All are stored as `self.<name>`.

**`async ingest(self, result: ScrapeResult, *, platform: str, job_type: str, target: str | None = None) -> IngestionReport`**
The public entry point (called by e.g. `app/services/scrape_service.py` after a scraper run
completes). Steps:
1. `self.scrape_job_repo.start(platform, job_type, target)` — creates a `ScrapeJob` row
   up front, before any data is processed, so a crash mid-run still leaves a record.
2. Runs `self._run(result, report)` inside a `try/except`.
3. On success with some non-fatal errors recorded → `scrape_job_repo.mark_partial(...)`.
4. On success with zero errors → `scrape_job_repo.mark_succeeded(...)`.
5. On an *uncaught* exception (something `_run` itself didn't isolate) →
   `scrape_job_repo.mark_failed(...)`, logs via `logger.exception`, and appends
   `f"fatal: {exc}"` to `report.errors` — this is the outermost safety net.
6. Always returns the `IngestionReport`, even on fatal failure, so the caller always gets a
   coherent summary instead of an exception bubbling into the UI.

**`async _run(self, result: ScrapeResult, report: IngestionReport) -> None`**
The actual orchestration, entity type by entity type, in dependency order (authors before
channels/posts, which are before videos/comments, which are before their derived data):
1. **Authors**: dedupe by key → bulk upsert (`_safe_bulk`) → build `author_id_map` →
   record count.
2. **Channels**: dedupe → remap each channel's `author_id` through `author_id_map` (via
   `model_copy(update=...)`, since Pydantic models are otherwise treated as immutable here)
   → bulk upsert → build `channel_id_map`.
3. **Posts**: same pattern, remapping `author_id`.
4. **Videos**: remaps both `channel_id` and (if present) `post_id` — a video can belong to a
   channel directly or be attached to a specific post.
5. **Comments**: remaps `post_id` and `author_id`; explicitly sets
   `parent_comment_id: None` for the first pass (see next step) because a reply's parent
   might not have a persisted id yet within the same batch.
6. `_relink_comment_parents(...)` — **second pass**: now that every comment in the batch has
   a persisted id, go back and patch `parent_comment_id` on replies via a direct
   `comment_repo.update(...)` call. Two passes are required because a reply and its parent
   can arrive in the same scrape batch in either order.
7. `_ingest_media`, `_ingest_hashtags`, `_ingest_mentions`, `_ingest_engagement` — each
   derives secondary rows from the already-persisted posts.
8. `_generate_embeddings(persisted_posts, persisted_comments, persisted_videos, report)` —
   last step; runs after everything else has succeeded/failed so embeddings only cover
   content that's actually in the DB.

**`async _relink_comment_parents(self, original_comments, comment_id_map, report) -> None`**
For each original (pre-remap) comment that had a `parent_comment_id`, looks up both the
comment's own new id and its parent's new id in `comment_id_map`; if both exist, calls
`comment_repo.update(new_id, {"parent_comment_id": new_parent_id})`. Wrapped in a
try/except per comment so one bad link doesn't stop the rest — appends to `report.errors`.

**`async _ingest_media(self, posts: list[Post], post_id_map, report) -> None`**
For each post with media: looks up existing media rows for that post
(`media_repo.by_post`), diffs by URL (`existing_urls`) so re-running ingestion on already-seen
posts doesn't create duplicate media rows, remaps `post_id` on the new ones via
`model_copy`, and `bulk_create_media`s only the new ones. Per-post try/except; errors
appended, not raised.

**`async _ingest_hashtags(self, posts, post_id_map, report) -> None`**
1. Collects the **set** of all distinct hashtag strings across every post in the batch
   (`{tag for post in posts for tag in post.hashtags}`), so a tag used by 50 posts is only
   upserted once.
2. Bulk-upserts them all as `Hashtag(tag=tag)` rows, builds `tag_id_map` (`tag -> persisted
   id`).
3. Second loop: for every post, for every one of its tags, builds a `PostHashtag(post_id=...,
   hashtag_id=...)` link row, then `bulk_link`s all of them at once.
   Both phases have independent try/except blocks — a failure upserting tags aborts hashtag
   processing for the whole batch (`return` inside the except), but a failure linking still
   only affects the link step.

**`async _ingest_mentions(self, posts, post_id_map, report) -> None`**
Mirrors `_ingest_media`'s dedup-against-existing pattern: fetches existing mentions for the
post, diffs by `username`, only creates new `Mention` rows for usernames not already linked.

**`async _ingest_engagement(self, posts, post_id_map, report) -> None`**
For each post, looks up `NORMALIZERS[post.platform]` (the platform-specific normalizer
object) and calls its `extract_engagement(post)` method (documented in
`docs/scraping_normalization_reference.md`), then remaps `post_id` and
`engagement_repo.upsert_for_post(...)`. This is the one `_ingest_*` step that depends on the
normalization layer rather than only on repositories — because "what counts as engagement" is
platform-specific (likes vs retweets vs views), the extraction logic lives with each
platform's normalizer, not in the pipeline itself.

**`async _generate_embeddings(self, posts, comments, videos, report) -> None`**
Builds a flat `list[EmbeddableItem]`:
- One item per post whose `caption or content` is non-blank, tagged `EmbeddingSourceType.POST`.
- One item per comment whose `content` is non-blank, tagged `EmbeddingSourceType.COMMENT`.
- One item per video whose `transcript` is non-blank, tagged `EmbeddingSourceType.TRANSCRIPT`.

If the combined list is empty, returns immediately (no API calls). Otherwise calls
`self.embedding_service.embed_batch(items)` and stores the returned count on
`report.embeddings_generated`. Wrapped in try/except — **embedding failures never block
already-persisted content**; they're recorded as a `report.errors` entry
(`"embedding generation failed: {exc}"`) and the pipeline still reports success for
everything upstream.

**`async _safe_bulk(self, fn, items, report, label) -> list[ModelT]`**
Generic helper used by every entity type's upsert call: if `items` is empty, short-circuits
to `[]` (avoids an unnecessary network round-trip); otherwise calls `await fn(items)` inside a
try/except, and on failure logs a warning, appends `f"{label} batch upsert failed: {exc}"` to
`report.errors`, and returns `[]` so downstream `_build_id_map` calls just get an empty map
rather than crashing.

**Called from**: `app/services/scrape_service.py` constructs an `IngestionPipeline` and calls
`.ingest(...)` after running a scraper (see `docs/services_ui_reference.md` for the exact
call site) — this is the sole external entry point into the whole pipeline module.

---

## `app/embeddings/service.py`

### `checksum_of(text: str) -> str`
`hashlib.sha256(text.encode("utf-8")).hexdigest()`. A free function (not a method) because
it's pure, stateless, and needed both for computing a new checksum and — implicitly, via
`EmbeddingRepository.get_by_checksum` — for comparison against a previously stored one.

### `EmbeddableItem` (`@dataclass(slots=True, frozen=True)`)
One unit of text to embed, tied back to its source record: `source_type`
(`EmbeddingSourceType`), `source_id` (`str`), `platform` (`PlatformName`), `text` (`str`),
`metadata: dict | None = None`. Deliberately a plain dataclass, **not** a Pydantic
`BaseSchema` subclass — `frozen=True` (immutable) + `slots=True` (no `__dict__`, lower memory)
because these are constructed in bulk (one per post/comment/transcript, potentially hundreds
per ingestion run) and never need to be mutated or JSON-(de)serialized on their own. The only
place `EmbeddableItem` is constructed is `app/ingestion/pipeline.py::_generate_embeddings`
(and directly in `tests/`, and presumably `scripts/backfill_embeddings.py` per the class
docstring on `EmbeddingService`).

### `EmbeddingService`
Docstring: "Batch-oriented embedding pipeline used by ingestion after every scrape, and
available standalone for backfills (`scripts/backfill_embeddings.py`)."

**`__init__(self, provider=None, document_repo=None, embedding_repo=None) -> None`**
- `provider: EmbeddingProvider | None` → defaults to `OpenAIEmbeddingProvider()`.
- `document_repo: DocumentRepository | None` → defaults to `DocumentRepository()`.
- `embedding_repo: EmbeddingRepository | None` → defaults to `EmbeddingRepository()`.
All optional/injectable for testability (mock the provider to avoid real API calls in tests).

**`async embed_batch(self, items: list[EmbeddableItem]) -> int`**
Returns the number of items *actually* re-embedded (a skipped/unchanged item does not count,
since no network call was made for it). Steps:
1. **Filter + checksum pass**: for each item, strip `text`; skip if blank. Compute
   `checksum = checksum_of(text)`. Call
   `embedding_repo.get_by_checksum(item.source_id, item.source_type.value, provider.model_name)`
   — if a row exists **and** its `checksum` matches, log at debug level and `continue`
   (skip — this is the "don't pay to re-embed unchanged content" optimization). Otherwise
   append to `pending` and remember the checksum in a `dict[str, str]` keyed by `source_id`.
2. If `pending` is empty after filtering, return `0` immediately (no API call).
3. **One batched embedding call**: `provider.embed_texts([item.text.strip() for item in pending])`
   — a single network round-trip for the whole batch rather than one call per item.
4. **Build and upsert `Document` rows** (source-of-truth text) for every pending item, then
   `document_repo.bulk_upsert(documents, on_conflict="source_type,source_id")`.
5. **Re-key by the persisted documents**, not the locally-constructed ones. Inline comment
   explains why: the DB assigns/keeps the authoritative `id` on upsert (see
   `BaseRepository._serialize_for_upsert`, `docs/repositories_reference.md`), which differs
   from the client-generated one whenever the upsert updates an already-existing row. Also
   notes: `Document.source_type` reads back as a plain `str` (because `BaseSchema` sets
   `use_enum_values=True`), while `EmbeddableItem.source_type` stays a real
   `EmbeddingSourceType` enum (it's a dataclass, not a `BaseSchema`), hence the explicit
   `.value` access when building the lookup key `(item.source_type.value, item.source_id)`.
6. **Build `EmbeddingRow` objects**, one per pending item, referencing the just-resolved
   `document.id` as `document_id`, plus `model`/`dimensions` from the provider, the
   remembered `checksum`, the returned `vector`, and `metadata`.
7. `embedding_repo.bulk_upsert_embeddings(embedding_rows)`.
8. Logs `"Embedded batch"` with the count, returns `len(embedding_rows)`.

**`async embed_one(self, item: EmbeddableItem) -> int`**
Convenience wrapper: `return await self.embed_batch([item])`.

**Called from**: `app/ingestion/pipeline.py::_generate_embeddings` (the main path, after every
scrape); presumably `scripts/backfill_embeddings.py` for one-off backfills (per its own
docstring — check that script directly if you need its exact call shape).

---

## `app/embeddings/providers.py`

### `EmbeddingProvider` (`Protocol`, `@runtime_checkable`)
Structural-typing interface, not an ABC — deliberately, per the docstring: swapping providers
(OpenAI → a local sentence-transformers model, say) should never require touching
`app.embeddings.service`, it only needs an object with the right shape:
- `model_name: str`
- `dimensions: int`
- `async def embed_texts(self, texts: list[str]) -> list[list[float]]` — "Return one
  embedding vector per input text, same order as input" (order preservation is required
  because `EmbeddingService.embed_batch` zips `pending` items with the returned `vectors`
  by position via `zip(pending, vectors, strict=True)`).

### `OpenAIEmbeddingProvider`
The default concrete implementation, backed by `openai.AsyncOpenAI`.

**`__init__(self, model_name: str | None = None, dimensions: int | None = None) -> None`**
Reads `get_settings()` and falls back to `settings.openai_embedding_model` /
`settings.embedding_dimensions` when not explicitly overridden — lets tests or callers pin a
specific model without touching global config. Builds its own `AsyncOpenAI` client using
`settings.openai_api_key.get_secret_value()` (a Pydantic `SecretStr`, so the raw key is never
accidentally logged/repr'd elsewhere).

**`@with_retry(exceptions=(Exception,), max_attempts=3)` `async embed_texts(self, texts: list[str]) -> list[list[float]]`**
- Returns `[]` immediately for an empty input list (avoids a pointless API call).
- Calls `self._client.embeddings.create(model=self.model_name, input=texts)`.
- Any exception from the OpenAI SDK is caught and re-raised as the app's own
  `EmbeddingError(..., context={"count": len(texts)})` (`app.utils.exceptions` — see
  `docs/infra_utils_reference.md`) — so callers only ever need to catch one app-specific
  exception type regardless of the underlying provider's SDK.
- `with_retry` (`app.utils.retry`) wraps the whole call with up to 3 attempts on any
  exception — transient API/network failures get retried automatically before surfacing to
  the caller.
- Returns `[item.embedding for item in response.data]` — unwraps the OpenAI response shape
  into the plain `list[list[float]]` the `EmbeddingProvider` protocol promises.

**Called from**: constructed by default in `EmbeddingService.__init__` and
`RetrievalService.__init__` whenever no explicit provider is injected — i.e. this is the
provider used both when *writing* embeddings (ingestion) and when *embedding a query*
(retrieval).

---

## `app/retrieval/service.py`

Module docstring: "Hybrid retrieval: keyword search (Postgres `tsvector`), semantic search
(pgvector cosine similarity via the `match_embeddings` RPC), and metadata filtering
(platform/author/hashtag/date/popularity), combinable. This is the one place the AI assistant
goes to fetch 'relevant records' — it never queries `documents`/`embeddings`/`posts`
directly." That last sentence is the key architectural fact: `app/ai/assistant.py` (see
`docs/ai_reference.md`) is expected to call into `RetrievalService`, not repositories, when it
needs to ground an answer in scraped content.

### Module-level constants
```python
_KEYWORD_WEIGHT = 0.4
_SEMANTIC_WEIGHT = 0.6
```
Fixed weights used to combine keyword-match and semantic-match scores in `hybrid_search`.
Comment: semantic similarity is weighted higher because it degrades more gracefully on
paraphrased queries than plain keyword matching.

### `RetrievalService`

**`__init__(self, embedding_provider=None, embedding_repo=None, post_repo=None, author_repo=None, engagement_repo=None) -> None`**
All optional, all default-constructed (`OpenAIEmbeddingProvider()`, `EmbeddingRepository()`,
`PostRepository()`, `AuthorRepository()`, `EngagementRepository()`) — same DI-for-testability
pattern as `IngestionPipeline`/`EmbeddingService`.

**`async keyword_search(self, query: str, *, platform: str | None = None, limit: int = 20) -> list[RetrievalResult]`**
Full-text search over `documents.search_vector`. Implementation note in the docstring:
`text_search()` (postgrest-py) returns a stripped-down request builder that no longer exposes
`.eq()`/`.limit()` for further chaining — so platform filtering and the result cap are applied
**in Python** after the (already GIN-indexed) full-text match runs, not via additional
query-builder calls. Uses `options={"type": "web_search"}`, which maps to Postgres's
`websearch_to_tsquery` — chosen specifically because it accepts arbitrary natural-language
input (spaces, punctuation, a full question), whereas the default `to_tsquery` syntax requires
`word1 & word2`-style boolean operators and would raise a syntax error on a plain question
like "What has NASA posted about recently?" Runs the actual Supabase call via
`asyncio.to_thread(_run)` (the Supabase Python client is sync; this pattern appears throughout
the app to avoid blocking the event loop). Wraps any exception as `RetrievalError`. Returns a
list of `RetrievalResult` with `score=1.0` (keyword matches aren't ranked/scored by Postgres
here, so all keyword hits are treated as equally relevant before the hybrid-merge step
re-weights them) and `metadata={"match": "keyword"}`.

**`async semantic_search(self, query: str, *, platform: str | None = None, limit: int = 20) -> list[RetrievalResult]`**
Embeds the query itself: `provider.embed_texts([query])` (one-item batch — reuses the exact
same embedding provider/model used for stored content, which is required for cosine
similarity to be meaningful). If no vector comes back, returns `[]`. Otherwise calls
`embedding_repo.match(vectors[0], match_count=limit, platform=platform)` (the
`match_embeddings` Postgres RPC — see `docs/repositories_reference.md`), wraps failures as
`RetrievalError`, and maps each result row to a `RetrievalResult` with
`score=float(row["similarity"])` and `metadata={"match": "semantic"}`.

**`async hybrid_search(self, query: str, filters: RetrievalFilters | None = None, *, limit: int = 10) -> list[RetrievalResult]`**
1. Defaults `filters` to `RetrievalFilters()` if `None`.
2. Runs `keyword_search` and `semantic_search` **concurrently** via `asyncio.gather`, each
   asking for `limit * 2` candidates — over-fetching so that metadata filters (applied
   *after* retrieval, in `_apply_filters`) don't starve the final result set down below
   `limit`.
3. Merges both result lists into a `dict[(source_type, source_id), RetrievalResult]` keyed by
   `result.key` (a property on `RetrievalResult`, defined in `app/retrieval/models.py`):
   - Keyword-only hits: score becomes `result.score * _KEYWORD_WEIGHT`.
   - Semantic-only hits: score becomes `result.score * _SEMANTIC_WEIGHT`.
   - Hits present in **both**: the *existing* (keyword-derived) entry's score gets
     `+= result.score * _SEMANTIC_WEIGHT` added on top, and its `metadata["match"]` is
     overwritten to `"hybrid"` — so a hybrid hit's combined score is
     `keyword_score * 0.4 + semantic_score * 0.6`.
4. Runs `self._apply_filters(merged.values(), filters)`, then sorts descending by `score`,
   truncates to `limit`, and returns.

**`async popular_posts(self, *, platform: str | None = None, limit: int = 10) -> list[RetrievalResult]`**
Docstring: "Direct popularity ranking, bypassing text/vector search entirely — backs
questions like 'most liked posts this month' where there's no keyword/semantic query, only a
sort + filter." Fetches `engagement_repo.top_by_likes(limit=limit * 3)` (over-fetches 3x
because some engagement rows may have no matching post, or the wrong platform, and get
filtered out below), then for each engagement row: skips if `post_id is None`; fetches the
post via `post_repo.get_by_id`; skips if not found or platform doesn't match the filter;
otherwise builds a `RetrievalResult` with `score=float(engagement.likes or 0)` and
`metadata={"match": "popularity", "likes": ..., "views": ...}`. Stops early once `limit`
results are collected.

**`async _apply_filters(self, results: list[RetrievalResult], filters: RetrievalFilters) -> list[RetrievalResult]`**
Short-circuits (`return results` unchanged) if none of `filters.author_username`,
`.hashtag`, `.date_from`, `.date_to`, `.min_likes`, `.content_types` are set — avoids extra
DB round-trips when no filter is actually requested. Otherwise, for every result: non-`post`
source types pass through unfiltered (filters like hashtag/date/likes are post-specific
concepts); for `post` results, fetches the full `Post` row (`post_repo.get_by_id`) and checks
each active filter in turn (`content_type` membership, hashtag membership — case/`#`-
insensitive, `posted_at` range, author username — fetched via `author_repo.get_by_id` and
compared case/`@`-insensitively, and `min_likes` — fetched via
`engagement_repo.get_by_post`), excluding the result on the first failed check.

**Called from**: expected caller is `app/ai/assistant.py` (per the module docstring) and/or
`app/services/*` / the Gradio chat tab for user-facing search — see `docs/ai_reference.md`
and `docs/services_ui_reference.md` for the verified call sites.

---

## `app/retrieval/models.py`

Module docstring: "Value objects shared by every retrieval mode (keyword/semantic/hybrid)."
Both classes are plain `@dataclass(slots=True)` — not Pydantic `BaseSchema` models — since
they're internal, in-process value objects built and consumed entirely within
`RetrievalService`/`app/ai/assistant.py`, never serialized from/to raw JSON or a DB row
directly, so Pydantic's parsing/validation machinery isn't needed here.

### `RetrievalFilters`
Docstring: "Metadata filters combinable with any search mode. All fields are optional and
AND-combined — the assistant's SQL/retrieval planner fills in only the ones a user's question
implies (e.g. 'this month' -> date_from/date_to, 'on Instagram' -> platform)." This confirms
`app/ai/assistant.py` (or a component of it) is what actually constructs `RetrievalFilters`
from a parsed user question before calling `RetrievalService.hybrid_search` — see
`docs/ai_reference.md` for the verified construction site.

| Field | Type | Meaning |
|---|---|---|
| `platform` | `str \| None` | Restrict to one platform (`"instagram"`, etc.) |
| `author_username` | `str \| None` | Restrict to one author, matched case/`@`-insensitively in `RetrievalService._apply_filters` |
| `hashtag` | `str \| None` | Restrict to posts containing this tag, matched case/`#`-insensitively |
| `date_from` / `date_to` | `datetime \| None` | Inclusive range on `Post.posted_at` |
| `min_likes` | `int \| None` | Lower bound on `Engagement.likes` |
| `content_types` | `list[str] \| None` | Restrict to specific `ContentType` values (post/reel/tweet/etc.) |

All default to `None` (or, for `content_types`, an absent list) — `RetrievalService._apply_filters`
short-circuits and returns results unchanged when every field is unset, which is exactly the
"no filters implied by the question" case.

### `RetrievalResult`
Docstring: "One ranked hit, uniform across keyword/semantic/hybrid/popularity search so the
AI assistant never branches on which mode produced it." This is the single return shape for
all four `RetrievalService` search methods — the reason `hybrid_search` can merge keyword and
semantic hits into one `dict` and treat them identically.

| Field | Type | Meaning |
|---|---|---|
| `source_type` | `str` | e.g. `"post"`, `"comment"` — plain string, not the `EmbeddingSourceType` enum, since this models a generic search hit rather than an embedding record specifically |
| `source_id` | `str` | id of the underlying post/comment/etc. |
| `platform` | `str` | which platform the hit came from |
| `content` | `str` | the actual text shown to the user/assistant |
| `score` | `float` | ranking score — meaning depends on mode (`1.0` fixed for keyword, cosine similarity for semantic, weighted sum for hybrid, raw like-count for popularity) |
| `metadata` | `dict[str, Any]` (`default_factory=dict`) | mode tag (`{"match": "keyword"/"semantic"/"hybrid"/"popularity"}`) plus mode-specific extras (e.g. `likes`/`views` for popularity) |

**`key` (property, `-> tuple[str, str]`)**: `return (self.source_type, self.source_id)`. The
natural identity of a search hit, independent of which mode found it — this is exactly the
dict key `RetrievalService.hybrid_search` uses to detect when a keyword hit and a semantic hit
refer to the same underlying record, so their scores can be combined instead of both showing
up as separate results. Because `RetrievalResult` is a regular (non-frozen) dataclass,
`hybrid_search` mutates `existing.score` and `existing.metadata["match"]` in place rather than
constructing a new instance on merge.

---

## How it all connects (quick map)

```
scraper (app/apify/*)
   -> ScrapeResult
   -> IngestionPipeline.ingest()                [app/ingestion/pipeline.py]
        -> repositories: authors/channels/posts/videos/comments/media/hashtags/mentions/engagement
        -> EmbeddingService.embed_batch()       [app/embeddings/service.py]
             -> OpenAIEmbeddingProvider.embed_texts()   [app/embeddings/providers.py]
             -> DocumentRepository / EmbeddingRepository upserts

User query (AI assistant / Gradio chat)
   -> RetrievalService.hybrid_search() / semantic_search() / keyword_search() / popular_posts()
        [app/retrieval/service.py]
             -> OpenAIEmbeddingProvider.embed_texts()   (embeds the query)
             -> EmbeddingRepository.match()             (pgvector RPC)
             -> PostRepository / AuthorRepository / EngagementRepository  (filtering)
        -> list[RetrievalResult]                        [app/retrieval/models.py]
```
