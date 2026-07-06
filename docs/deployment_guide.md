# Deployment Guide

This project ships as a single Python process (the Gradio app) plus a Supabase-hosted Postgres
database and two external API dependencies (Apify, OpenAI). There is no separate backend server —
`app/gradio/app.py` is both the UI and the process that talks to Supabase/Apify/OpenAI. This guide
covers running that process in production.

## Running the Gradio app as a managed process

`scripts/launch_gradio.py` calls `app.gradio.app.main()`, which builds the `gr.Blocks` app and
calls `.launch(theme=gr.themes.Soft())`. In production, run it under a process manager rather than
directly in a terminal so it restarts on crash and its logs are captured.

### Option A: systemd

```ini
# /etc/systemd/system/social-intel.service
[Unit]
Description=Social Media Intelligence Platform (Gradio)
After=network.target

[Service]
Type=simple
User=appuser
WorkingDirectory=/opt/social-intel
EnvironmentFile=/opt/social-intel/.env
ExecStart=/opt/social-intel/.venv/bin/python scripts/launch_gradio.py
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now social-intel
sudo journalctl -u social-intel -f
```

Note `gr.Blocks.launch()` binds to Gradio's default host/port (`127.0.0.1:7860` unless overridden
by passing `server_name`/`server_port` to `.launch()` — this project's `main()` does not currently
pass either, so put a reverse proxy such as nginx in front of it for TLS termination and public
access, forwarding to `127.0.0.1:7860`).

### Option B: Docker

A minimal `Dockerfile` is included at the repository root for convenience:

```dockerfile
FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
EXPOSE 7860
CMD ["python", "scripts/launch_gradio.py"]
```

```bash
docker build -t social-intel .
docker run --rm -p 7860:7860 --env-file .env social-intel
```

This is a starting point, not a hardened production image — there is no multi-stage build, no
non-root user, and no health check configured. Treat it as optional polish; the systemd approach
above is the more directly supported path given the current repo layout (no `docker-compose.yml`,
no CI workflow building/pushing an image).

## Environment variable management in production

- **Never commit `.env`.** It is not present in this repository and should not be added to it;
  `.env.example` is the template checked into version control.
- Use your host's secret manager to inject the variables listed in
  `docs/environment_variables.md` at deploy time: AWS Secrets Manager / SSM Parameter Store, GCP
  Secret Manager, Azure Key Vault, or your platform's native secrets store (e.g. a systemd
  `EnvironmentFile` with restrictive permissions, as above, populated by your deployment tooling
  rather than hand-edited).
- `Settings` (`app/config/settings.py`) reads from `.env` via `pydantic-settings` but will also
  pick up real process environment variables of the same name — in most container/PaaS
  deployments you can skip the `.env` file entirely and inject environment variables directly.
- Rotate `APIFY_API_TOKEN`, `SUPABASE_KEY`, and `OPENAI_API_KEY` independently; none of them are
  derived from each other.

## Supabase migration workflow for production

Two supported ways to apply `migrations/*.sql` against a production database:

1. **`scripts/run_migrations.py` pointed at the production `SUPABASE_DB_URL`.** This applies every
   file in `migrations/` (in filename order) inside a single transaction via SQLAlchemy. Run it
   from a trusted admin environment (CI job or bastion host with the production
   `SUPABASE_DB_URL` injected only for that job), never from a developer's default `.env`.
2. **Supabase's SQL Editor.** Copy each migration file's contents into the dashboard's SQL editor
   and run them in order (`0001` through `0004`). This is a reasonable fallback when direct
   Postgres connectivity to production isn't available from wherever you run deployment tooling.

Either way, migrations are idempotent by construction — every `CREATE TABLE`/`CREATE INDEX`/
`CREATE EXTENSION` uses `if not exists`, and the seed `INSERT INTO platforms` uses
`ON CONFLICT (name) DO NOTHING` — so re-running the full set against an already-migrated database
is safe.

There is no separate down-migration/rollback tooling in this repository; treat schema changes as
forward-only and write new migration files (e.g. `0005_...sql`) for any correction, following the
existing numbering convention.

## Logging in production

`app/logging/logger.py`'s `configure_logging()` already writes rotating, machine-parseable JSON
logs to `LOG_DIR`:

- `app.jsonl` — every log at `LOG_LEVEL` and above, rotated at 10 MB, retained 14 days.
- `errors.jsonl` — `ERROR`-level and above only, rotated at 10 MB, retained 30 days.

Both sinks use `enqueue=True` (safe for multi-threaded/async log calls) and apply the same
sensitive-key redaction filter as the console sink. In production:

- Mount/persist `LOG_DIR` outside the container's writable layer if running under Docker (a
  volume), so logs survive container restarts.
- Ship `LOG_DIR/*.jsonl` to a log aggregation service (e.g. an ELK/OpenSearch stack, Grafana Loki,
  Datadog, or your cloud provider's log ingestion agent) by pointing a log-forwarder (Filebeat,
  Vector, Fluent Bit) at the directory — no code changes are required since the files are already
  structured JSON lines.
- Set `LOG_LEVEL=INFO` (or `WARNING`) in production; `DEBUG` is verbose and includes per-item
  ingestion/embedding-skip messages not usually needed outside local development.

## Production readiness notes

- There is currently no CI/CD workflow committed to this repository (`.github/` exists but is
  empty) and no automated test-gate on merges — `pytest`, `ruff check app/`, `black --check app/`,
  and `mypy app/` must be run manually or wired into your own CI system before deploying.
- `fastapi`/`uvicorn` are present in `requirements.txt` but no HTTP API is wired up (see
  `docs/api_documentation.md`) — there is nothing to deploy behind a WSGI/ASGI server today beyond
  the Gradio process itself.
- Because `Assistant`/`SQLGenerator`/`RetrievalService` each construct their own OpenAI client
  eagerly in `__init__`, make sure `OPENAI_API_KEY` is present in the deployed environment before
  the first chat request — a missing key surfaces as an `AssistantError` on the first `ask()` call,
  not at process startup.
