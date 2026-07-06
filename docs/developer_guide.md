# Developer Guide

Practical guidance for contributing to this codebase: adding a platform, adding a new entity, the
coding conventions actually in use, how to run tests/linters, and how the ingestion pipeline's
error isolation works.

## Adding a new platform

The scraper registry (`app/apify/__init__.py`) is the only integration point. Concretely, to add
a platform (using Reddit as the example — its `PlatformName.REDDIT` value already exists and is
already seeded into the `platforms` table by `migrations/0002_core_content_tables.sql`, but has no
scraper registered):

1. **Write the normalizer.** Create `app/normalization/reddit.py` with functions matching the
   pattern in `app/normalization/instagram.py`: `normalize_author(raw: dict) -> Author`,
   `normalize_post(raw: dict, *, author_id: str) -> Post`, `normalize_comment(raw: dict, *,
   post_id: str, author_id: str, parent_id: str | None = None) -> Comment`, and
   `extract_engagement(post: Post) -> Engagement`. Use `app.normalization.common.first_present`/
   `as_int` to read fields defensively (raw Apify actor output is not guaranteed stable across
   actor versions).
2. **Write the scraper.** Create `app/apify/reddit/scraper.py` with a class:

   ```python
   from app.apify import register_scraper
   from app.apify.base.scraper import BaseScraper, ScrapeResult
   from app.models.pydantic.enums import PlatformName

   @register_scraper(PlatformName.REDDIT)
   class RedditScraper(BaseScraper):
       platform = "reddit"

       async def scrape_profile(self, identifier: str) -> ScrapeResult: ...
       async def scrape_posts(self, identifier: str, *, limit: int = 50) -> ScrapeResult: ...
       async def scrape_comments(self, post_url_or_id: str, *, limit: int = 100) -> ScrapeResult: ...
       # scrape_hashtag / scrape_keyword: only override if the platform's Apify actor
       # supports it; otherwise the BaseScraper default NotImplementedError is correct
       # (see YouTubeScraper, which overrides neither).
   ```

   `scrape_profile`/`scrape_posts`/`scrape_comments` are abstract on `BaseScraper` and must be
   implemented; `scrape_hashtag`/`scrape_keyword` have a default body that raises
   `NotImplementedError(f"{self.platform} does not support hashtag search")` and should only be
   overridden if the platform actually has that capability.
3. **Register the module for import.** Add `reddit` to the import line at the bottom of
   `app/apify/__init__.py`: `from app.apify import instagram, twitter, youtube, reddit  # noqa:
   E402, F401` — the `@register_scraper` decorator only runs when the module is imported, and
   `get_scraper()`/`registered_platforms()` only see what has been imported.
4. **Register the normalizer module.** Add `PlatformName.REDDIT: reddit` to the `NORMALIZERS` dict
   in `app/normalization/__init__.py` so `IngestionPipeline._ingest_engagement` can find
   `extract_engagement` for posts on this platform.
5. **Nothing else changes.** `app/ingestion`, `app/services`, `app/retrieval`, and `app/ai` only
   ever reach a platform through `get_scraper(platform)` and `NORMALIZERS[platform]`, both keyed by
   `PlatformName` — no branch-by-platform-name code exists anywhere above the scraper layer.
6. Add the new actor's env vars (e.g. `APIFY_REDDIT_SCRAPER_ACTOR`) to `Settings`
   (`app/config/settings.py`) and `.env.example`, following the existing naming convention.

## Adding a new Pydantic model + repository + migration

1. **Model**: add a new file under `app/models/pydantic/`, subclassing whatever mix of
   `IdentifiedMixin`/`TimestampMixin`/`SoftDeleteMixin`/`BaseSchema` the entity needs (see
   `app/models/pydantic/base.py`). Add a `dedup_key` computed field if the entity participates in
   upsert-based deduplication (every entity the ingestion pipeline currently upserts has one). Add
   the new model to `app/models/pydantic/__init__.py`'s imports and `__all__`.
2. **Migration**: add a new numbered file under `migrations/` (e.g. `0005_<name>.sql`), following
   the conventions already used: `uuid` primary key defaulting to `uuid_generate_v4()`,
   `created_at`/`updated_at timestamptz not null default now()`, `deleted_at timestamptz` if the
   entity supports soft delete, a `set_updated_at()` trigger if it has `updated_at`, and explicit
   indexes for any column you'll filter/sort on regularly. `scripts/run_migrations.py` applies
   every file in `migrations/` in filename order — never renumber or edit an already-applied file.
3. **SQLAlchemy Core metadata**: add a matching `Table(...)` definition to `app/models/db/orm.py`
   so the new table name lands in `KNOWN_TABLES` — without this, any AI-generated SQL referencing
   the new table is rejected by `validate_sql_tables` as an "unknown table."
4. **Schema description**: add a one-line entry to `SCHEMA_DESCRIPTION` in
   `app/database/schema_metadata.py` so the AI assistant's SQL generation prompt knows the new
   table exists. Run `python scripts/print_schema.py` afterward to sanity-check the description and
   `KNOWN_TABLES` haven't drifted apart.
5. **Repository**: add `app/repositories/<entity>_repository.py` subclassing
   `BaseRepository[YourModel]`, setting `table_name`/`model`, and adding whatever query methods the
   entity needs beyond the inherited CRUD (see `app/repositories/hashtag_repository.py` for a
   repository with a bespoke aggregate query, or `app/repositories/post_repository.py` for one with
   several filtered-list methods).

## Coding conventions observed in this codebase

- **Type hints everywhere.** `mypy` is configured with `disallow_untyped_defs = true`
  (`pyproject.toml`); every function signature in `app/` has full type annotations, and
  `from __future__ import annotations` is used throughout for forward references.
- **Docstrings explain *why*, not *what*.** Nearly every module and non-trivial function has a
  docstring explaining the architectural reasoning behind a choice (e.g. why `BaseRepository`
  dispatches through `asyncio.to_thread`, why `EmbeddingProvider` is a `Protocol` rather than an
  ABC) rather than restating what the code obviously does. Follow this pattern for new code:
  comment the *decision*, not the mechanics.
- **`model_copy(update=...)` instead of mutation.** Pydantic models in this codebase are treated
  as immutable value objects; every place a field needs to change after construction (id
  remapping in `IngestionPipeline._run`, attaching a transcript in
  `YouTubeScraper.scrape_posts`) uses `obj.model_copy(update={...})` rather than assigning to an
  attribute in place.
- **Async repository methods via `asyncio.to_thread`.** `supabase-py`'s underlying client is
  synchronous; every repository method that touches it wraps the call in a nested `def _run():
  ...` function passed to `await asyncio.to_thread(_run)` (see any method in
  `app/repositories/base.py` or `app/repositories/post_repository.py`). Follow this exact pattern
  for any new repository method — do not call the synchronous client directly from an `async def`.
- **Defensive raw-field reading in normalizers.** `app.normalization.common.first_present(raw, *keys,
  default=...)` is used everywhere a platform's Apify actor might expose the same concept under
  different key names across actor versions (`commentsCount` vs `commentCount`), instead of a long
  `if/elif` chain.
- **Structural typing (`Protocol`) for swappable providers.** `EmbeddingProvider`
  (`app/embeddings/providers.py`) is a `Protocol`, not an ABC, specifically so a new provider needs
  no inheritance relationship with the existing one — just a matching `embed_texts`/`model_name`/
  `dimensions` surface.
- **One exception hierarchy.** Every custom exception derives from `AppError`
  (`app/utils/exceptions.py`); catch the narrowest subclass that's meaningful for your call site
  (e.g. `UnsafeSQLError` specifically, not a bare `except Exception`) unless you genuinely want to
  isolate *any* failure from that call (as the ingestion pipeline's per-step methods do).

## Running tests and linters

```bash
pytest                    # runs with --cov=app --cov-report=term-missing (configured in pyproject.toml)
ruff check app/
black app/
mypy app/
```

The test suite (`tests/`) currently contains `tests/conftest.py` (shared Pydantic-model factory
fixtures: `make_author`, `make_post`, `make_comment`, `make_media`, `make_engagement`) and
`tests/unit/test_models.py`; `tests/integration/` and `tests/mocks/` exist as package scaffolding
with no test modules yet. Coverage percentage will vary as tests are added — run `pytest` locally
to see the current number rather than trusting a stale figure in documentation.

`pyproject.toml` also configures `ruff` (`select = ["E", "F", "I", "UP", "B", "SIM", "C4"]`,
`ignore = ["E501"]`, first-party import sorting for `app`), `black` (line length 100, target
`py312`), and `mypy` (`plugins = ["pydantic.mypy"]`, `exclude = ["tests/", "scripts/"]`). Run all
three before opening a PR; there is no CI workflow currently enforcing this automatically (see
`docs/deployment_guide.md`).

## How the ingestion pipeline's error isolation works

`IngestionPipeline.ingest()` (`app/ingestion/pipeline.py`) wraps its entire `_run()` call in one
`try/except`: if something genuinely unexpected escapes every inner safeguard, the job is marked
`failed` and the exception is swallowed into `report.errors` as `"fatal: {exc}"` rather than
propagating to the caller.

Inside `_run()`, almost every step is already individually isolated:

- **Bulk upserts** (`author_repo.bulk_upsert_authors`, `post_repo.bulk_upsert_posts`, etc.) go
  through `_safe_bulk()`, which catches any exception, appends `f"{label} batch upsert failed:
  {exc}"` to `report.errors`, logs a warning, and returns an empty list — the pipeline continues
  with whatever *did* upsert (e.g. if posts fail but authors succeeded, comments/media/hashtags
  referencing those authors still get a chance to persist against whatever id-mapping exists).
- **Per-post/per-comment steps** (`_ingest_media`, `_ingest_hashtags`, `_ingest_mentions`,
  `_ingest_engagement`, `_relink_comment_parents`) each catch their own exceptions per iteration
  (or per batch, for hashtags) and append a descriptive error string rather than raising —
  one post's media failing to insert never stops the next post's media from being processed.
- **Embedding generation** (`_generate_embeddings`) is wrapped in its own `try/except`: embedding
  failures (e.g. OpenAI outage) never block already-persisted content — the scrape "succeeded"
  from a content standpoint even if embeddings are temporarily unavailable.

After `_run()` returns (whether or not it collected errors), `ingest()` checks
`report.errors`: if non-empty, the job is marked `partial` (via `ScrapeJobRepository.mark_partial`,
storing the first 20 errors joined together) instead of `failed`. Only an exception that survives
all of the above (e.g. a bug in `_run()`'s own control flow, not in any of the operations it
delegates to) reaches the outer `try/except` and marks the job `failed`. This means: **a
"failed" scrape job is a real, unexpected bug; a "partial" one is expected, recoverable, everyday
degradation** (a single hashtag failing to upsert, one comment's parent-link update failing, etc.)
— check `report.errors` / the `scrape_jobs.error` column to see exactly what degraded.
