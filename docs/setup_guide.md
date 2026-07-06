# Setup Guide

Step-by-step instructions to get the platform running locally from a fresh clone.

## Prerequisites

- **Python 3.12 or newer** (`pyproject.toml` declares `requires-python = ">=3.12"`).
- **A Supabase project** (free tier is enough to start) — you will need its REST URL, an API key,
  and its direct Postgres connection string.
- **An Apify account and API token** — https://console.apify.com/account/integrations. The default
  actors referenced in `.env.example` (`apify/instagram-profile-scraper`,
  `apidojo/tweet-scraper`, `streamers/youtube-scraper`, etc.) must be accessible to your account
  (Apify Store actors are usable by any account, some require a paid Apify plan for large runs).
- **An OpenAI API key** — https://platform.openai.com/api-keys — used both for chat completions
  (`OPENAI_CHAT_MODEL`, default `gpt-4o-mini`) and embeddings (`OPENAI_EMBEDDING_MODEL`, default
  `text-embedding-3-small`).

## 1. Clone and create a virtual environment

```bash
git clone <repo-url>
cd scrapper

python -m venv .venv
```

Activate it:

```bash
# Windows (PowerShell / cmd)
.venv\Scripts\activate

# macOS / Linux
source .venv/bin/activate
```

## 2. Install dependencies

For local development (includes pytest, ruff, black, mypy on top of the runtime dependencies):

```bash
pip install -r requirements-dev.txt
```

For a production-only install, `pip install -r requirements.txt` is sufficient (see
`docs/deployment_guide.md`).

## 3. Configure `.env`

Copy the template and fill in real values:

```bash
cp .env.example .env
```

Every variable in `.env.example` is documented in `docs/environment_variables.md`. At minimum, to
run a real scrape + chat session you need:

- `APIFY_API_TOKEN`
- `SUPABASE_URL`, `SUPABASE_KEY`, `SUPABASE_DB_URL`
- `OPENAI_API_KEY`

`app/config/settings.py` (`Settings`) loads `.env` automatically via `pydantic-settings`
(`env_file=".env"`); nothing else needs to source it manually. Settings are cached process-wide via
`get_settings()` (an `lru_cache`d factory), so changing `.env` requires restarting the process.

**Important distinction**: `SUPABASE_URL` + `SUPABASE_KEY` are the PostgREST REST API credentials
used by `supabase-py` (every repository in `app/repositories` uses these). `SUPABASE_DB_URL` is a
*different* thing — the direct Postgres connection string (found in Supabase's dashboard under
Project Settings -> Database -> Connection string), used only by two things: `scripts/run_migrations.py`
and the read-only SQLAlchemy engine (`app/database/sql_engine.py`) that executes AI-generated SQL.
You need both.

## 4. Run database migrations

```bash
python scripts/run_migrations.py
```

This applies every file in `migrations/` (in filename order: `0001_extensions_and_functions.sql`,
`0002_core_content_tables.sql`, `0003_chat_and_logging_tables.sql`,
`0004_embeddings_and_documents.sql`) directly against `SUPABASE_DB_URL` via SQLAlchemy, inside a
single transaction. It requires `SUPABASE_DB_URL` to be set and will exit with a clear error
message if it isn't. This is separate from (and does not use) the read-only SQL guard in
`app/database/sql_engine.py` — that guard exists only to protect against AI-generated SQL, not
trusted admin tooling like this script.

Migration `0001` enables the `uuid-ossp`, `vector` (pgvector), and `pg_trgm` Postgres extensions —
your Supabase project must allow extension creation (the default for Supabase-hosted projects).

## 5. Verify the setup

```bash
python scripts/print_schema.py
```

This prints the `SCHEMA_DESCRIPTION` string the AI assistant uses to ground its SQL generation
prompt, plus the full list of tables known to `app/models/db/orm.py`'s `KNOWN_TABLES` whitelist
(used to reject hallucinated table references in AI-generated SQL). It does not require an open
database connection — it is a good quick check that your Python environment and imports are
healthy before touching Supabase or Apify at all.

To confirm migrations actually applied, check the Supabase dashboard's Table Editor, or connect
with `psql "$SUPABASE_DB_URL"` and run `\dt`.

## 6. Run a first scrape

```bash
python scripts/run_scrape.py instagram posts nasa --limit 10
```

This exercises the full pipeline end to end: `ScrapeService` -> `InstagramScraper` -> Apify actor
-> normalization -> `IngestionPipeline` -> Supabase -> `EmbeddingService` -> OpenAI -> `embeddings`
table. On success it prints an `IngestionReport` summary (records upserted per entity, hashtags
linked, mentions created, embeddings generated, and any errors). See
`docs/example_workflows.md` for more scrape examples across all three platforms and modes
(`profile`, `posts`, `comments`, `hashtag`, `keyword` — YouTube does not support `hashtag`/
`keyword`).

## 7. Launch the Gradio UI

```bash
python scripts/launch_gradio.py
```

This builds and launches the `gr.Blocks` app from `app/gradio/app.py` (Chat tab + Analytics tab)
using `gr.themes.Soft()`. Gradio prints the local URL to launch (typically
`http://127.0.0.1:7860`) — open it in a browser. The Blocks graph builds successfully even with no
credentials configured; only actual button clicks (asking a question, refreshing analytics) hit
Supabase/OpenAI and will surface a friendly inline error if credentials are missing or invalid.

## Troubleshooting

- **`DatabaseConnectionError: Supabase credentials are not configured`** — `SUPABASE_URL`/
  `SUPABASE_KEY` are empty or unset in `.env`. Confirm you restarted the process after editing
  `.env` (settings are cached).
- **`SystemExit: SUPABASE_DB_URL is not configured`** (from `run_migrations.py`) — set
  `SUPABASE_DB_URL` to the direct Postgres connection string, not the REST URL.
- **`UnsupportedPlatformError`** — the platform argument passed to `run_scrape.py` isn't
  `instagram`, `twitter`, or `youtube` (the only three with a registered scraper today, even
  though `PlatformName` reserves slots for `reddit`/`linkedin`/`facebook`/`tiktok`/`news`).
- **Apify actor run fails/times out** — check the actor ID env vars
  (`APIFY_INSTAGRAM_PROFILE_ACTOR`, etc. — see `docs/environment_variables.md`) are actors your
  Apify account can actually run; some require a paid Apify plan for larger `resultsLimit`/
  `maxItems` values.
