# Environment Variables

Sourced from `app/config/settings.py` (the `Settings` class, loaded via `pydantic-settings` from
`.env`) and `.env.example`. Every field listed below is real and present in both files unless
noted otherwise. Names are case-insensitive (`case_sensitive=False` in `Settings.model_config`);
unrecognized env vars are silently ignored (`extra="ignore"`).

| Variable | Required / Optional | Default | Description | Where to obtain it |
|---|---|---|---|---|
| `APP_ENV` | Optional | `development` | One of `development`, `staging`, `production`, `test`. Drives `Settings.is_production`. | Set per deployment environment. |
| `LOG_LEVEL` | Optional | `INFO` | Loguru log level for both console and file sinks. | Any standard level (`DEBUG`, `INFO`, `WARNING`, `ERROR`). |
| `LOG_DIR` | Optional | `logs` | Directory Loguru writes `app.jsonl` and `errors.jsonl` rotating log files into (created if missing). | Any writable path. |
| `APIFY_API_TOKEN` | Required for scraping | `""` (empty) | Apify API token used by `ApifyActorRunner` (`app/apify/base/client.py`) to authenticate every actor run. | Apify Console -> Settings -> Integrations. |
| `APIFY_INSTAGRAM_PROFILE_ACTOR` | Optional | `apify/instagram-profile-scraper` | Actor ID used by `InstagramScraper.scrape_profile`. | Apify Store; override if using a custom/forked actor. |
| `APIFY_INSTAGRAM_POST_ACTOR` | Optional | `apify/instagram-post-scraper` | Actor ID used by `InstagramScraper.scrape_posts`. | Apify Store. |
| `APIFY_INSTAGRAM_HASHTAG_ACTOR` | Optional | `apify/instagram-hashtag-scraper` | Actor ID used by `InstagramScraper.scrape_hashtag`. | Apify Store. |
| `APIFY_INSTAGRAM_COMMENT_ACTOR` | Optional | `apify/instagram-comment-scraper` | Actor ID used by `InstagramScraper.scrape_comments`. | Apify Store. |
| `APIFY_TWITTER_SCRAPER_ACTOR` | Optional | `apidojo/tweet-scraper` | Actor ID used by every `TwitterScraper` method (profile/posts/comments/hashtag/keyword all funnel through this one search-driven actor). | Apify Store. |
| `APIFY_YOUTUBE_SCRAPER_ACTOR` | Optional | `streamers/youtube-scraper` | Actor ID used by `YouTubeScraper.scrape_profile`/`scrape_posts` (channel/video listing). | Apify Store. |
| `APIFY_YOUTUBE_COMMENT_ACTOR` | Optional | `streamers/youtube-comments-scraper` | Actor ID used by `YouTubeScraper.scrape_comments`. | Apify Store. |
| `APIFY_YOUTUBE_TRANSCRIPT_ACTOR` | Optional | `pintostudio/youtube-transcript-scraper` | Actor ID used for best-effort per-video transcript fetches (only attempted when `limit <= 10` in `scrape_posts`). | Apify Store. |
| `SUPABASE_URL` | Required for persistence | `""` (empty) | REST API base URL for your Supabase project, used by `supabase-py` (`app/database/supabase_client.py`) for all repository CRUD via PostgREST. | Supabase Dashboard -> Project Settings -> API -> Project URL. |
| `SUPABASE_KEY` | Required for persistence | `""` (empty) | API key paired with `SUPABASE_URL` (service role or anon key, depending on your RLS setup). | Supabase Dashboard -> Project Settings -> API. |
| `SUPABASE_DB_URL` | Required for migrations + AI SQL | `""` (empty) | Direct Postgres connection string (distinct from `SUPABASE_URL`). Used by `scripts/run_migrations.py` and the read-only SQLAlchemy engine (`app/database/sql_engine.py`) that executes AI-generated SQL. | Supabase Dashboard -> Project Settings -> Database -> Connection string. |
| `OPENAI_API_KEY` | Required for AI features | `""` (empty) | Used by the `Assistant` (chat completions), `SQLGenerator` (SQL generation), and `OpenAIEmbeddingProvider` (embeddings). | https://platform.openai.com/api-keys |
| `OPENAI_CHAT_MODEL` | Optional | `gpt-4o-mini` | Chat completion model used by `Assistant` and `SQLGenerator`. | Any OpenAI chat-capable model name your account has access to. |
| `OPENAI_EMBEDDING_MODEL` | Optional | `text-embedding-3-small` | Embedding model used by `OpenAIEmbeddingProvider`. Must match `EMBEDDING_DIMENSIONS`. | Any OpenAI embedding model name. |
| `EMBEDDING_DIMENSIONS` | Optional | `1536` | Vector dimensionality; must match the `vector(1536)` column type in `migrations/0004_embeddings_and_documents.sql` and the chosen `OPENAI_EMBEDDING_MODEL`'s native dimension count. Changing this requires a migration to alter the `embeddings.vector` column. | Determined by `OPENAI_EMBEDDING_MODEL` choice. |
| `MAX_CONCURRENT_SCRAPES` | Optional | `5` | Declared cap on concurrent scrape operations. | Tune based on Apify plan concurrency limits. |

## Notes

- All `SecretStr`-typed fields (`APIFY_API_TOKEN`, `SUPABASE_KEY`, `OPENAI_API_KEY`) are masked in
  Python `repr()`/logging by Pydantic itself, and any log `extra` key whose name contains `token`,
  `api_key`, `password`, `secret`, `authorization`, or `key` is separately redacted to
  `***REDACTED***` by the Loguru filter in `app/logging/logger.py`.
- `Settings` exposes three convenience properties used elsewhere in the codebase to check whether
  a credential group is actually populated before attempting a call: `has_apify_credentials`,
  `has_supabase_credentials`, `has_openai_credentials`.
- Leaving any of the required variables empty does not crash the app at import time — for
  example, the Gradio Blocks graph builds successfully with no credentials configured at all (see
  `app/gradio/analytics_tab.py`'s and `app/gradio/chat_tab.py`'s comments on lazy client
  construction). The corresponding `DatabaseConnectionError`/`AssistantError` is raised only when a
  button click actually tries to reach Supabase/Apify/OpenAI.
