# Architecture

This document explains the actual layering of the codebase under `app/`, why each layer is
separated from its neighbors, the extensibility mechanism for new platforms, the dual persistence
path (PostgREST vs. read-only SQLAlchemy), and a handful of design decisions worth understanding
before modifying the code.

## Layering

```
config -> database -> models (pydantic + db) -> repositories -> apify (scrapers)
       -> normalization -> ingestion (pipeline) -> embeddings -> retrieval -> ai (assistant)
       -> services -> gradio (UI)
```

`utils`, `logging`, and `prompts` are cross-cutting: every layer above may import them, but they
never import back up the stack.

### `app/config`

`app/config/settings.py` defines one `Settings` (Pydantic `BaseSettings`) class, loaded once via
`get_settings()` (an `lru_cache`d factory). Every other module reads configuration through this
single object instead of touching `os.environ` directly, so there is exactly one place that knows
about environment variable names, defaults, and secret handling (`SecretStr` for tokens/keys).

### `app/database`

Two independent ways to reach Postgres, kept in the same package because they share the same
target database but serve very different callers:

- `supabase_client.py` — a cached `supabase-py` `Client`, used by every repository for ordinary
  CRUD via PostgREST.
- `sql_engine.py` — a cached, read-only SQLAlchemy `Engine` connected directly via
  `SUPABASE_DB_URL`, used exclusively by the AI assistant's generated SQL, with `assert_sql_is_safe`
  / `validate_sql_tables` guarding it (see "Dual persistence path" below).
- `schema_metadata.py` — a static, hand-maintained `SCHEMA_DESCRIPTION` string used to ground the
  SQL-generation prompt without a live DB round-trip.

### `app/models`

`app/models/pydantic/` holds every domain entity (`Author`, `Post`, `Comment`, `Channel`, `Video`,
`Media`, `Hashtag`, `Mention`, `Engagement`, `Platform`, `Conversation`, `ChatMessage`, `QueryLog`,
`AssistantLog`, `EmbeddingDocument`) as fully-typed Pydantic v2 models, sharing common mixins
(`IdentifiedMixin` for a client-generated UUID, `TimestampMixin`, `SoftDeleteMixin`) from
`app/models/pydantic/base.py`. These are the types that flow through scrapers, normalization, and
the ingestion pipeline.

`app/models/db/orm.py` is a *second*, deliberately separate representation: SQLAlchemy Core
`Table` objects mirroring `migrations/*.sql`. It is not a persistence path — repositories never use
it — it exists purely so `KNOWN_TABLES` (the set of real table names) can validate AI-generated SQL
without a live database connection, and so `scripts/print_schema.py` has one source of truth to
render.

Separating "domain model" (Pydantic) from "SQL schema shape" (SQLAlchemy Core) means the AI safety
layer and the application layer can each evolve independently as long as both track
`migrations/*.sql`.

### `app/repositories`

One repository class per table (`AuthorRepository`, `PostRepository`, `CommentRepository`,
`ChannelRepository`/`VideoRepository`, `MediaRepository`, `HashtagRepository`/
`PostHashtagRepository`, `MentionRepository`, `EngagementRepository`, `ConversationRepository`,
`MessageRepository`, `QueryLogRepository`/`AssistantLogRepository`, `ScrapeJobRepository`,
`DocumentRepository`/`EmbeddingRepository`, `PlatformRepository`), all inheriting from
`BaseRepository[ModelT]` (`app/repositories/base.py`). This is the only layer that imports
`app.database.supabase_client` — nothing above it talks to Postgres directly. That isolation is
what lets `app/ingestion`, `app/retrieval`, and `app/services` be tested against fake repositories
without a real database.

### `app/apify`

`app/apify/base/scraper.py` defines `BaseScraper` (an ABC with `scrape_profile`, `scrape_posts`,
`scrape_comments` as abstract methods, and `scrape_hashtag`/`scrape_keyword` with a default
`NotImplementedError` body for platforms that don't support them) and `ScrapeResult` (a dataclass
grouping `posts`/`authors`/`comments`/`media`/`channels`/`videos`/`raw_item_count` produced by one
scrape call). `app/apify/base/client.py` (`ApifyActorRunner`) wraps the synchronous `apify-client`
SDK in `asyncio.to_thread` so every scraper's actor calls are non-blocking.

Each platform package (`app/apify/instagram`, `app/apify/twitter`, `app/apify/youtube`) implements
one `BaseScraper` subclass that maps that platform's Apify actor conventions onto the common
interface, then delegates raw-item -> Pydantic conversion to `app/normalization`. This is the
layer where platform-specific knowledge (actor IDs, input shapes, field naming quirks) is allowed
to live; nothing downstream ever branches on platform name except through the small
`NORMALIZERS` / `_REGISTRY` lookup tables described below.

### `app/normalization`

Pure functions (`normalize_author`, `normalize_post`, `normalize_comment`, `extract_engagement`,
...) per platform module (`instagram.py`, `twitter.py`, `youtube.py`), plus shared helpers in
`common.py` (`dedupe_by_key`, `merge_prefer_non_null`, `first_present`, `as_int`/`as_float`) used
by every normalizer to read inconsistently-named raw fields defensively. Keeping normalization
separate from scraping means a normalizer can be unit-tested against a raw JSON fixture with no
network access and no Apify client at all.

### `app/ingestion`

`IngestionPipeline` (`app/ingestion/pipeline.py`) is the architectural core connecting scraping to
persistence. It is described in detail below and in `docs/sequence_diagrams.md`; the short version
is: dedupe each entity list by its `dedup_key`, upsert authors first, remap every downstream
entity's foreign keys to the *persisted* (not locally-generated) ids, upsert the rest in FK order,
relink comment parent ids in a second pass, then build `media`/`hashtags`/`mentions`/`engagement`
rows and trigger embedding generation — isolating failures at every step so one bad batch never
aborts the whole run.

### `app/embeddings`

`EmbeddingProvider` (`app/embeddings/providers.py`) is a `Protocol`, not an ABC, specifically so a
future non-OpenAI provider (e.g. a local sentence-transformers model) can be swapped in without
`app/embeddings/service.py` changing at all — it only needs an object with `embed_texts` and
`model_name`/`dimensions`. `EmbeddingService.embed_batch` is the checksum-aware batch pipeline used
both by ingestion (after every scrape) and available standalone for ad hoc backfills.

### `app/retrieval`

`RetrievalService` (`app/retrieval/service.py`) is the *only* place anything above it — including
the AI assistant — goes to fetch "relevant records." It never lets a caller query `documents`,
`embeddings`, or `posts` directly. It implements `keyword_search` (Postgres `tsvector` full-text),
`semantic_search` (pgvector cosine similarity via the `match_embeddings` RPC), `hybrid_search`
(both, merged and weighted), and `popular_posts` (a pure popularity ranking bypassing text/vector
search for questions like "most liked posts"). `RetrievalFilters`/`RetrievalResult`
(`app/retrieval/models.py`) are the uniform value objects every mode returns so the assistant never
branches on which mode produced a given hit.

### `app/ai`

`SQLGenerator` (`app/ai/sql_generator.py`) turns a natural-language question into SQL grounded in
`SCHEMA_DESCRIPTION`, validates it through `app.database.assert_sql_is_safe`, and executes it via
`execute_readonly_sql`. `Assistant` (`app/ai/assistant.py`) is the top-level orchestrator: it tries
SQL generation+execution, falls back to retrieval-only if that fails, always runs
`RetrievalService.hybrid_search` as well, builds a combined context block, calls the OpenAI chat
completion, and persists the full turn (user message, assistant message, query log, assistant log)
regardless of which paths succeeded.

### `app/prompts`

Plain `.format()`-style string templates (`SQL_GENERATION_PROMPT`, `ASSISTANT_SYSTEM_PROMPT`,
`CONVERSATION_MEMORY_PROMPT`, plus reusable summarization/trend/sentiment/cross-platform templates)
rather than a templating engine — every prompt here has a small fixed set of named placeholders,
so pulling in Jinja2 or LangChain's prompt abstractions would be exactly the "unnecessary
framework" the project spec warns against.

### `app/services`

The orchestration layer consumed by the UI and CLI: `ScrapeService` (bridges `app.apify` scraping
to `app.ingestion` persistence), `ChatService` (thin wrapper over `Assistant` +
conversation/message repositories for the Gradio chat tab), `AnalyticsService` (read-only
aggregation queries for the Gradio analytics tab). Services contain no database or HTTP client
code of their own — they only call repositories, the pipeline, and the assistant.

### `app/gradio`

`app/gradio/app.py` builds a `gr.Blocks` app with two tabs (`chat_tab.py`, `analytics_tab.py`).
The UI layer is intentionally thin: every button click calls into `ChatService`/`AnalyticsService`
and nothing else, so the UI never touches `app.ai`, `app.retrieval`, or `app.database` directly.

### Cross-cutting: `app/utils`, `app/logging`, `app/prompts`

`app/utils/exceptions.py` defines one `AppError` hierarchy (`ScraperError`, `RepositoryError`,
`EmbeddingError`, `RetrievalError`, `AssistantError`/`SQLGenerationError`/`UnsafeSQLError`, ...) so
callers can catch broad or narrow failure classes, and the ingestion pipeline can distinguish
recoverable errors (log + skip) from fatal ones. `app/utils/retry.py` centralizes a single Tenacity
retry policy (`with_retry`) reused by the Apify runner and the OpenAI embedding provider.
`app/logging/logger.py` configures Loguru once (Rich console sink for local dev, rotating JSON
file sinks for machine-parseable production logs, with a filter that redacts anything that looks
like a token/key/secret) behind a single `get_logger(__name__)` call.

## Extensibility: adding a new platform

The registry in `app/apify/__init__.py` is the single seam through which `app.services` and
`app.ingestion` reach a platform's scraper:

```python
_REGISTRY: dict[PlatformName, type[BaseScraper]] = {}

def register_scraper(platform: PlatformName) -> Callable[[type[BaseScraper]], type[BaseScraper]]:
    def _decorator(cls: type[BaseScraper]) -> type[BaseScraper]:
        _REGISTRY[platform] = cls
        return cls
    return _decorator

def get_scraper(platform: PlatformName | str) -> BaseScraper:
    key = PlatformName(platform)
    if key not in _REGISTRY:
        raise UnsupportedPlatformError(f"No scraper registered for platform {key!r}")
    return _REGISTRY[key]()
```

Adding Reddit (say) requires, concretely:

1. Add `REDDIT = "reddit"` to `PlatformName` in `app/models/pydantic/enums.py` (already present —
   several future platforms are pre-declared and already seeded into the `platforms` table by
   `migrations/0002_core_content_tables.sql`, but have no scraper registered yet).
2. Write `app/normalization/reddit.py` with `normalize_author`/`normalize_post`/`normalize_comment`/
   `extract_engagement` functions, following the pattern in `app/normalization/instagram.py`.
3. Write `app/apify/reddit/scraper.py` with a `RedditScraper(BaseScraper)` class decorated
   `@register_scraper(PlatformName.REDDIT)`, implementing whichever of
   `scrape_profile`/`scrape_posts`/`scrape_comments`/`scrape_hashtag`/`scrape_keyword` Reddit's
   Apify actors support (methods that don't apply simply aren't overridden — the base class's
   `NotImplementedError` is correct for them, as YouTube already demonstrates for
   `scrape_hashtag`/`scrape_keyword`).
4. Add `from app.apify import reddit` to the import list at the bottom of `app/apify/__init__.py`
   (next to `instagram, twitter, youtube`) so the `@register_scraper` decorator actually runs.
5. Add `NORMALIZERS[PlatformName.REDDIT] = reddit` in `app/normalization/__init__.py` so
   `IngestionPipeline._ingest_engagement` can find `extract_engagement` for the new platform.

Nothing in `app/ingestion`, `app/services`, `app/retrieval`, or `app/ai` needs to change — they
only ever go through `get_scraper()` and `NORMALIZERS`, both keyed by `PlatformName`. This mirrors
success criteria #21 ("Extensibility") in `specs.md`.

## Dual persistence path

Two separate ways to reach the same Postgres database, deliberately kept apart:

- **PostgREST via `supabase-py`** (`app/database/supabase_client.py`, consumed exclusively by
  `app/repositories`) — used for all normal application CRUD: creates, upserts, filtered lists,
  soft deletes. This is what the ingestion pipeline and services use.
- **A read-only SQLAlchemy `Engine`** (`app/database/sql_engine.py`), connected directly via
  `SUPABASE_DB_URL` (the direct Postgres connection string, not the PostgREST URL) — used
  exclusively by the AI assistant's SQL generator to run arbitrary `SELECT`/`WITH` statements the
  repository layer has no pre-built method for.

The second path exists because natural-language questions need arbitrary ad hoc queries (joins,
aggregates, `GROUP BY`) that a fixed repository method surface can't anticipate. Since that SQL is
LLM-generated, it is treated as untrusted input and passed through `assert_sql_is_safe` before
execution:

- only statements starting with `select` or `with` are allowed;
- no semicolon-separated multiple statements;
- a fixed forbidden-keyword list (`insert`, `update`, `delete`, `drop`, `alter`, `truncate`,
  `grant`, `revoke`, `create`, SQL comment markers) rejects the statement outright if present;
- `validate_sql_tables` regex-scans every `FROM`/`JOIN` table reference against `KNOWN_TABLES`
  (from `app/models/db/orm.py`) and rejects hallucinated table names before the query ever reaches
  Postgres.

If any check fails, `UnsafeSQLError` is raised; `Assistant.ask()` catches this (and any other SQL
failure) and falls back to retrieval-only rather than surfacing the failure to the end user.

## Key design decisions

**Why `id` is stripped before upsert.** Every Pydantic model generates a fresh client-side UUID
(`IdentifiedMixin`, so records can be referenced — e.g. for embedding linkage — before they are
persisted). `BaseRepository._serialize_for_upsert` (`app/repositories/base.py`) drops `id` from the
payload before an upsert: a naive upsert that included `id` would try to overwrite the existing
row's primary key on every re-ingestion of the same natural key (e.g. the same Instagram post
scraped twice), which Postgres rejects once any child row (`comments.author_id`, `media.post_id`,
...) has a foreign key pointing at that id. Dropping `id` lets Postgres keep the existing row's
primary key on `UPDATE` and fall back to the column default (`uuid_generate_v4()`) on `INSERT`.
This is also why `IngestionPipeline._build_id_map` matches local-to-persisted ids by each model's
stable `dedup_key` (e.g. `f"{platform}:{platform_post_id}"`) rather than by list position or by the
client-generated id — the id that made it to the database is very often not the one generated
client-side.

**Why YouTube produces both a `Post` and a `Video` row.** Every other platform's content maps
one-to-one into the unified `posts` table so retrieval and the AI assistant can query "all content"
without a platform-specific branch. YouTube videos, however, carry fields (duration, transcript)
that don't belong on the generic `Post` schema without polluting it for every other platform. The
YouTube scraper (`app/apify/youtube/scraper.py`) therefore normalizes each raw item into *both* a
`Post` (so it slots into the shared content/retrieval model) and a `Video` (`post_id` foreign key
back to that `Post`, `channel_id` back to a `Channel`) for the duration/transcript-specific fields.
The same reasoning applies to `Author` + `Channel`: every video's embedded channel info produces a
generic `Author` row plus a `Channel` row carrying subscriber-count semantics.

**Why embeddings are keyed by checksum.** `EmbeddingService.embed_batch`
(`app/embeddings/service.py`) computes a SHA-256 checksum of each item's text and compares it
against the checksum stored on the most recent `embeddings` row for that `(source_id, source_type,
model)` before calling the embedding API. If the text hasn't changed since the last run, the item
is skipped entirely — no OpenAI call, no write. This matters because ingestion runs repeatedly
re-process the same posts/comments (a profile re-scrape re-fetches unchanged captions), and
re-embedding unchanged text on every run would multiply OpenAI API cost and latency for zero
benefit; the `unique (source_id, source_type, model)` constraint on `embeddings`
(`migrations/0004_embeddings_and_documents.sql`) is what makes the checksum lookup a single indexed
row fetch rather than a scan.
