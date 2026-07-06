# `app/` code reference — master index

Every `.py` file under `app/` is now documented, file by file: every import, class, function
(arguments + return types), field, and validator explained with *why* it exists, plus
grep-verified "where is this actually called from" citations (file:line) rather than
name-based guesses. Use this page as the table of contents; each linked document is
self-contained and can be read independently.

| Document | Covers |
|---|---|
| [`models_reference.md`](models_reference.md) | `app/models/pydantic/*` (all domain models: `Author`, `Channel`/`Video`, `Comment`/`Reply`/`Thread`, `Conversation`/`ChatMessage`/`QueryLog`/`AssistantLog`, `Engagement`, `Hashtag`/`Mention`/`PostHashtag`, `Media`, `Platform`, `Post`, shared enums, base mixins) + `app/models/db/orm.py` |
| [`embedding_model_explained.md`](embedding_model_explained.md) | Deep dive on `app/models/pydantic/embedding.py`'s `EmbeddingDocument` specifically, and how it differs from the repository-layer `Document`/`EmbeddingRow` actually used at runtime |
| [`repositories_reference.md`](repositories_reference.md) | `app/repositories/*` — `BaseRepository[T]` and every entity-specific repository (upsert/dedup/FK-remap conventions) |
| [`scraping_normalization_reference.md`](scraping_normalization_reference.md) | `app/apify/*` (base client/scraper + Instagram/Twitter/YouTube scrapers) and `app/normalization/*` (per-platform normalizers, the `NORMALIZERS` registry) |
| [`pipeline_reference.md`](pipeline_reference.md) | `app/ingestion/pipeline.py`, `app/embeddings/service.py` + `providers.py`, `app/retrieval/service.py` + `models.py` — the orchestration core connecting scraping → persistence → embeddings → search |
| [`ai_reference.md`](ai_reference.md) | `app/ai/assistant.py`, `app/ai/sql_generator.py`, `app/prompts/templates.py` — the natural-language assistant, SQL generation + safety checks, and prompt templates |
| [`services_ui_reference.md`](services_ui_reference.md) | `app/services/*` (`ScrapeService`, `ChatService`, `AnalyticsService`) and `app/gradio/*` (`app.py`, `chat_tab.py`, `analytics_tab.py`) |
| [`infra_utils_reference.md`](infra_utils_reference.md) | `app/config/settings.py`, `app/database/*` (Supabase client, schema metadata, SQL engine), `app/logging/logger.py`, `app/utils/*` (exceptions, retry, text) |

---

## How a request flows through the app, end to end

**Scraping → storage → embeddings** (background/CLI-triggered):
```
scripts/run_scrape.py (or similar CLI)
  -> ScrapeService.run(...)                         [services_ui_reference.md]
       -> get_scraper(platform)                     [scraping_normalization_reference.md]
       -> platform Scraper.scrape() -> ScrapeResult  [scraping_normalization_reference.md]
       -> IngestionPipeline.ingest(result, ...)      [pipeline_reference.md]
            -> repositories.*.bulk_upsert_*          [repositories_reference.md]
            -> NORMALIZERS[platform].extract_engagement
            -> EmbeddingService.embed_batch          [pipeline_reference.md]
                 -> OpenAIEmbeddingProvider.embed_texts
                 -> DocumentRepository / EmbeddingRepository upserts
```

**User asks a question** (Gradio chat UI):
```
app/gradio/chat_tab.py                               [services_ui_reference.md]
  -> ChatService.ask(...)
       -> Assistant.ask(...)                         [ai_reference.md]
            -> SqlGenerator.generate_and_execute(...) -> app/database/sql_engine.py (validated, read-only)
            -> RetrievalService.hybrid_search(...)    [pipeline_reference.md]
                 -> keyword_search (Postgres tsvector) + semantic_search (pgvector RPC), merged/weighted
            -> OpenAI chat completion, grounded in retrieval + SQL results
            -> persists ChatMessage / QueryLog / AssistantLog
```

**Analytics dashboard** (Gradio analytics tab): reads directly through `AnalyticsService` →
repositories, bypassing the AI/retrieval layer entirely — pure aggregation queries.

---

## Cross-cutting findings (dead code, drift, and asymmetries worth knowing)

These surfaced repeatedly while verifying "where is X actually called" across every file,
and are useful context if you're about to build on or refactor this codebase:

- **`EmbeddingDocument`** (`app/models/pydantic/embedding.py`) is fully implemented and unit
  tested but never instantiated by the live pipeline — the real runtime shape is
  `Document`/`EmbeddingRow` in `app/repositories/embedding_repository.py`. See
  `embedding_model_explained.md`.
- **The Gradio UI never triggers a scrape.** `ScrapeService` is fully built and tested, but
  its only caller is a CLI script — the Analytics tab only *reads* past `scrape_jobs`, it has
  no "run a scrape" button. See `services_ui_reference.md`.
- **`HashtagRepository.trending`'s docstring overstates what it does** — it claims a
  join-count over `post_hashtags` but the actual query just orders `hashtags` by
  `created_at`, no join/count involved. See `repositories_reference.md`.
- **`PlatformRepository` has zero callers anywhere in `app/`** — platform identity is carried
  entirely via the `PlatformName` enum on rows, not a FK to a `platforms` table lookup. Same
  for the `Platform` Pydantic model itself (never instantiated outside tests). See
  `models_reference.md` / `repositories_reference.md`.
- **`app/models/db/orm.py`'s detailed `Column`/`ForeignKey` definitions have drifted from the
  real migrations** (missing columns on several tables) — only its `KNOWN_TABLES` allowlist is
  actually consumed anywhere (by the SQL-safety check in `app/database/sql_engine.py`, used by
  `sql_generator.py`). See `models_reference.md`.
- **Several "obviously useful" helpers are defined, tested, and never called in production**:
  `ScrapeResult.merge()`/`merge_prefer_non_null()`, `guess_language()` in `app/utils/text.py`
  (despite its docstring claiming it's shared by every normalizer), `Settings.is_production`/
  `has_apify_credentials`/`has_openai_credentials`, `ValidationFailedError`/
  `NormalizationError`, and computed properties like `Post.has_media`/`Comment.is_reply`/
  `Engagement.engagement_rate` (the pipeline re-derives these inline instead of calling them).
  See `scraping_normalization_reference.md` and `infra_utils_reference.md` for the full list.
- **`Reply`/`Thread`** (`app/models/pydantic/comment.py`) are fully defined but the ingestion
  pipeline builds nested comments as plain `Comment` objects instead — only exercised in
  tests. See `models_reference.md`.
- **SQL safety is defense-in-depth**: `sql_generator.py` validates generated SQL twice — once
  in `generate_and_execute`, again inside `app/database/sql_engine.py::execute_readonly_sql`
  — enforcing SELECT/WITH-only, single-statement, a forbidden-keyword regex, and the
  `KNOWN_TABLES` allowlist. See `ai_reference.md`.

None of the above are bugs per se — they're either forward-looking API surface, UI/CLI scope
decisions, or convenience methods exercised only by tests — but they're the kind of thing
worth knowing before you assume "this function is called from X" just because it looks like
it should be.
