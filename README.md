# Social Media Intelligence Platform

A modular Python platform that scrapes Instagram, X (Twitter), and YouTube via Apify, normalizes
the results into a unified Pydantic/Postgres schema in Supabase, and exposes an AI assistant
(SQL generation + hybrid retrieval) over a Gradio chat + analytics UI.

## Features

- **Multi-platform scraping** — Instagram, X/Twitter, and YouTube via Apify actors, behind a
  single `BaseScraper` interface (profile / posts / comments / hashtag / keyword search).
- **Unified data model** — every platform's raw JSON is normalized into shared Pydantic models
  (`Author`, `Post`, `Comment`, `Channel`, `Video`, `Media`, `Hashtag`, `Mention`, `Engagement`)
  and persisted into a normalized Postgres schema in Supabase.
- **Resilient ingestion pipeline** — deduplication, foreign-key id remapping, comment
  parent-linking, and per-step error isolation so one bad batch never aborts a whole scrape job.
- **Embedding pipeline** — checksum-aware embedding generation (OpenAI) so unchanged content is
  never re-embedded; stored alongside full-text search vectors for hybrid retrieval.
- **Hybrid retrieval** — keyword (Postgres `tsvector`), semantic (pgvector cosine similarity),
  and popularity-based search, combinable with platform/author/hashtag/date/likes filters.
- **AI assistant** — answers natural-language questions by generating read-only SQL (validated
  and executed against a dedicated read-only engine) and/or retrieving relevant records, always
  grounded in retrieved context and cited, with full conversation memory.
- **Chat UI** — a Gradio app with a chat tab (conversation sidebar, search, export to Markdown,
  new/clear chat) and an analytics tab (platform distribution, trending hashtags, top authors,
  engagement leaders, recent scrape jobs, AI query stats).
- **Every interaction logged** — conversations, messages, query logs, and assistant logs are all
  persisted to Supabase for auditing.

## Architecture

The codebase follows a layered, clean-architecture style:

```
config -> database -> models (pydantic + db) -> repositories -> apify (scrapers)
       -> normalization -> ingestion (pipeline) -> embeddings -> retrieval -> ai (assistant)
       -> services -> gradio (UI)
```

with `utils`, `logging`, and `prompts` as cross-cutting concerns used by every layer.

See [`docs/architecture.md`](docs/architecture.md) for the full breakdown, extensibility story,
and key design decisions, [`docs/er_diagram.md`](docs/er_diagram.md) for the database schema, and
[`docs/sequence_diagrams.md`](docs/sequence_diagrams.md) for the scrape and chat flows.

## Quickstart

```bash
git clone <repo-url>
cd scrapper

python -m venv .venv
# Windows
.venv\Scripts\activate
# macOS/Linux
source .venv/bin/activate

pip install -r requirements-dev.txt

cp .env.example .env
# then fill in APIFY_API_TOKEN, SUPABASE_URL/KEY/DB_URL, OPENAI_API_KEY (see docs/environment_variables.md)

python scripts/run_migrations.py
python scripts/launch_gradio.py
```

The Gradio app launches on the default local URL Gradio prints to the console (typically
`http://127.0.0.1:7860`).

## Project structure

```
app/
  ai/             SQL generator + the conversational Assistant orchestrator
  apify/          BaseScraper interface, registry, and per-platform scrapers
    base/         ApifyActorRunner (async wrapper over apify-client) + BaseScraper/ScrapeResult
    instagram/    InstagramScraper
    twitter/      TwitterScraper
    youtube/      YouTubeScraper
  config/         Centralized Pydantic Settings
  database/       Supabase client factory, read-only SQLAlchemy engine + SQL safety checks
  embeddings/     EmbeddingProvider protocol, OpenAI provider, EmbeddingService
  gradio/         Gradio Blocks app: chat tab + analytics tab
  ingestion/      IngestionPipeline: dedup, FK remap, error isolation, embedding trigger
  logging/        Loguru + Rich structured logging setup
  models/
    pydantic/     Domain models (Author, Post, Comment, Channel, Video, ...)
    db/           SQLAlchemy Core table metadata mirroring migrations/
  normalization/  Per-platform raw-JSON -> Pydantic mapping functions
  prompts/        Reusable LLM prompt templates
  repositories/   One repository per table, isolating Supabase/PostgREST access
  retrieval/      RetrievalService: keyword/semantic/hybrid/popularity search
  services/       ScrapeService, ChatService, AnalyticsService (orchestration layer)
  utils/          Exceptions, retry policy (Tenacity), text extraction helpers
migrations/       Hand-written SQL migrations (extensions, core tables, chat/logging, embeddings)
scripts/          CLI entrypoints (run_scrape, run_migrations, print_schema, launch_gradio)
tests/            pytest suite (unit / integration / mocks)
docs/             Architecture, ER diagram, sequence diagrams, setup/deployment/dev guides
```

## Example CLI usage

```bash
# Scrape the last 50 posts from an Instagram profile
python scripts/run_scrape.py instagram posts nasa --limit 50

# Scrape tweets under a hashtag
python scripts/run_scrape.py twitter hashtag climate --limit 100

# Scrape comments on a YouTube video
python scripts/run_scrape.py youtube comments dQw4w9WgXcQ --limit 200
```

Each run prints an `IngestionReport` summary: records upserted per entity, hashtags/mentions
linked, embeddings generated, and any errors encountered (the run still completes and is marked
`partial` rather than aborting).

## Testing

```bash
pytest
```

`pytest` is configured (in `pyproject.toml`) to run with `--cov=app --cov-report=term-missing`
by default. The test suite is under active development; run it locally to see current coverage
rather than relying on a fixed number in this README.

## Linting and type checking

```bash
ruff check app/
black app/
mypy app/
```

## Tech stack

- Python 3.12+
- [Apify client](https://pypi.org/project/apify-client/) for scraping
- [Supabase](https://supabase.com/) (Postgres + pgvector) via `supabase-py` (PostgREST) and
  SQLAlchemy (read-only, for AI-generated SQL)
- Pydantic v2 / Pydantic Settings for typed models and configuration
- httpx, Tenacity (retries), python-dotenv
- Loguru + Rich for structured logging
- Gradio for the chat + analytics UI
- OpenAI SDK for chat completions and embeddings
- pandas (analytics dataframes)
- `fastapi` / `uvicorn` / `langchain-core` are present in `requirements.txt` but are currently
  unused — reserved for an optional future HTTP API layer (see `docs/api_documentation.md`)

## Further documentation

- [`docs/architecture.md`](docs/architecture.md) — layering, extensibility, key design decisions
- [`docs/er_diagram.md`](docs/er_diagram.md) — database schema
- [`docs/sequence_diagrams.md`](docs/sequence_diagrams.md) — scrape and chat flows
- [`docs/setup_guide.md`](docs/setup_guide.md) — full setup walkthrough
- [`docs/deployment_guide.md`](docs/deployment_guide.md) — running this in production
- [`docs/environment_variables.md`](docs/environment_variables.md) — every config variable
- [`docs/api_documentation.md`](docs/api_documentation.md) — internal Python API reference
- [`docs/developer_guide.md`](docs/developer_guide.md) — extending the platform
- [`docs/example_workflows.md`](docs/example_workflows.md) — end-to-end walkthroughs
