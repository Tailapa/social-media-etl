# `app/services/*` and `app/gradio/*` — line-by-line and how they fit the app

This document covers the two layers that sit directly underneath the Gradio UI:

- **`app/services/*`** — the orchestration layer. Every service takes its
  dependencies (repositories, the AI assistant, the ingestion pipeline) as
  optional constructor kwargs (dependency injection for testability) and
  exposes a small, UI-shaped async API. Nothing above this layer (Gradio, a
  future CLI/admin action) is allowed to import a repository, `app.ai`,
  `app.ingestion`, or `app.apify` directly — see the module docstrings of
  `chat_service.py` and `scrape_service.py`, which state this boundary
  explicitly.
- **`app/gradio/*`** — the UI layer. `app.py` assembles two tabs
  (`chat_tab.py`, `analytics_tab.py`) into one `gr.Blocks` app; each tab
  module is pure Gradio wiring (components + event handlers) that calls into
  exactly one service and never touches a repository, `app.ai`, or `app.apify`
  itself.

Every claim below about "where X is used" was verified with `grep` across
`app/`, `scripts/`, and `tests/` and by reading the actual call sites — not
inferred from naming.

---

## 1. `app/services/__init__.py`

```python
from app.services.analytics_service import AnalyticsService
from app.services.chat_service import ChatService
from app.services.scrape_service import ScrapeService

__all__ = ["AnalyticsService", "ChatService", "ScrapeService"]
```

A pure re-export module — the standard "package facade" pattern used
throughout this codebase (the same pattern `app/models/pydantic/__init__.py`
uses for domain models, per the embedding-model reference doc). Its only
purpose is to let callers write `from app.services import ChatService`
instead of reaching into the submodule directly. It defines no behavior of
its own.

**Where used:** nothing in `app/`, `scripts/`, or `tests/` currently imports
from the package root (`app.services`) — every real import in the codebase
goes to the submodule directly, e.g. `from app.services.chat_service import
ChatService` (`app/gradio/chat_tab.py:21`), `from app.services.analytics_service
import AnalyticsService` (`app/gradio/analytics_tab.py:18`), `from
app.services.scrape_service import ScrapeService` (`scripts/run_scrape.py:23`).
So today `__init__.py` is inert plumbing — present for API ergonomics/future
callers, not exercised by any current import path.

---

## 2. `app/services/analytics_service.py`

Module docstring (lines 1–7) states the file's one design decision up front:
PostgREST (Supabase's REST layer over Postgres) can't express a `GROUP BY
... COUNT(*)` directly through its query builder, so any "count grouped by
X" dashboard stat is computed by fetching a bounded result set and reducing
it in Python. The docstring explicitly points at
`HashtagRepository.trending` as the precedent for this pattern elsewhere in
the repo, so this file isn't introducing a new approach, it's reusing an
established one.

### Imports

```python
import asyncio
from typing import Any

from app.models.pydantic import Author, Engagement
from app.models.pydantic.enums import PlatformName
from app.repositories.author_repository import AuthorRepository
from app.repositories.comment_repository import CommentRepository
from app.repositories.engagement_repository import EngagementRepository
from app.repositories.hashtag_repository import HashtagRepository
from app.repositories.post_repository import PostRepository
from app.repositories.query_log_repository import QueryLogRepository
from app.repositories.scrape_job_repository import ScrapeJob, ScrapeJobRepository
```

- `asyncio` — used only for `asyncio.gather`, to run independent read queries
  concurrently rather than sequentially (this file never awaits one query
  before starting the next).
- `Author`, `Engagement` — Pydantic domain models, used purely as return-type
  annotations (`list[Author]`, `list[Engagement]`) so callers get IDE/type
  checking on what a service method hands back.
- `PlatformName` — the `StrEnum` of supported platforms (`instagram`,
  `twitter`, `youtube`, ...), iterated over in `platform_distribution` to
  build one `count()` call per platform.
- Seven repository imports — one per table this dashboard reads from. Each
  repo is a thin, single-table wrapper around Supabase (`BaseRepository`
  subclass); `AnalyticsService` never talks to Supabase directly, only through
  these.
- `ScrapeJob` — imported alongside `ScrapeJobRepository` purely as a return
  type on `recent_scrape_jobs`.

### `class AnalyticsService`

```python
def __init__(
    self,
    post_repo: PostRepository | None = None,
    comment_repo: CommentRepository | None = None,
    author_repo: AuthorRepository | None = None,
    engagement_repo: EngagementRepository | None = None,
    hashtag_repo: HashtagRepository | None = None,
    scrape_job_repo: ScrapeJobRepository | None = None,
    query_log_repo: QueryLogRepository | None = None,
) -> None:
    self.post_repo = post_repo or PostRepository()
    ...
```

Seven optional repo params, each defaulted to a freshly constructed repo if
omitted. This is the "inject for tests, default-construct for production"
pattern used by every service and by `IngestionPipeline`/`Assistant` in this
codebase — `tests/integration/test_services.py:192-203` relies on it directly,
passing seven hand-written fakes (`FakeCountRepo`, `FakeAuthorRepo`,
`FakeHashtagRepo`, `FakeEngagementRepo`, `FakeScrapeJobRepo`,
`FakeQueryLogRepo`) instead of touching Supabase.

**Methods** (all `async`, all read-only — this class never writes to the DB):

- **`total_posts() -> int`** — `self.post_repo.count()`. Delegates to
  `BaseRepository.count()` (`app/repositories/base.py:190`), which issues a
  PostgREST `select("id", count="exact")` — the count is computed
  server-side, no rows are pulled over the wire.
- **`total_comments() -> int`** — same, via `comment_repo.count()`.
- **`platform_distribution() -> dict[str, int]`** — fires one
  `post_repo.count(filters={"platform": p.value})` per `PlatformName` member
  **concurrently** via `asyncio.gather(*(...))`, then zips the platform
  values back onto the results with `strict=True` (raises if the two
  sequences ever have different lengths — a correctness guard, since
  `gather` preserves input order). Six platforms → six concurrent COUNT
  queries instead of one query with a `GROUP BY`, because PostgREST can't
  express the latter (see module docstring).
- **`most_active_authors(*, limit: int = 10) -> list[Author]`** — delegates
  to `AuthorRepository.most_active` (`app/repositories/author_repository.py:37-41`),
  which orders by `post_count` descending. Keyword-only `limit` (the `*`)
  is a deliberate style choice repeated on every method here so call sites
  can never accidentally pass a positional integer that reads as unclear at
  the call site (`most_active_authors(5)` vs `most_active_authors(limit=5)`).
- **`trending_hashtags(*, limit: int = 10) -> list[dict[str, Any]]`** —
  delegates to `HashtagRepository.trending` (`app/repositories/hashtag_repository.py:24-39`),
  which — per its own docstring — is the exact "fetch bounded set, reduce in
  Python" pattern this file's module docstring points at. Note it returns
  raw `dict`s (`response.data` from Supabase), not a Pydantic model, because
  it selects only `id, tag` columns, not a full `Hashtag` row.
- **`top_engagement_posts(*, limit: int = 10) -> list[Engagement]`** —
  `EngagementRepository.top_by_likes` (`app/repositories/engagement_repository.py:18-21`),
  ordered by `likes` descending, with `include_deleted=True` (engagement
  rows aren't soft-deleted the way conversations are, so this flag just
  passes through `BaseRepository.list_all`'s default soft-delete filter).
- **`recent_scrape_jobs(*, limit: int = 20) -> list[ScrapeJob]`** —
  `ScrapeJobRepository.recent` (`app/repositories/scrape_job_repository.py:79-87`),
  ordered by `created_at` descending. This is the **only** place a scrape
  job's status is surfaced anywhere in the UI (see §8 below) — the
  Analytics tab is read-only visibility into jobs, not a way to start one.
- **`ai_query_stats(*, limit: int = 200) -> dict[str, Any]`** — fetches the
  200 most recent `QueryLog` rows via `QueryLogRepository.recent`
  (`app/repositories/query_log_repository.py:16-19`), then computes
  `total_queries` (`len(logs)`) and `avg_latency_ms` (mean of any
  non-`None` `latency_ms`, rounded to 2 dp, or `None` if there's no latency
  data at all — deliberately not `0`, since "no data" and "zero latency" are
  different facts). `QueryLog.latency_ms` is populated by
  `Assistant.ask` (`app/ai/assistant.py:190-214`) on every chat turn, so this
  stat is really "how is the AI assistant performing," despite living in
  `AnalyticsService` rather than `ChatService`.
- **`dashboard_summary() -> dict[str, Any]`** — the single method the
  Gradio Analytics tab actually calls
  (`app/gradio/analytics_tab.py:123`, inside `_refresh`). Runs all eight
  methods above **concurrently** with one `asyncio.gather` call and returns
  one dict with eight named keys (`total_posts`, `total_comments`,
  `platform_distribution`, `most_active_authors`, `trending_hashtags`,
  `top_engagement_posts`, `recent_scrape_jobs`, `ai_query_stats`). Bundling
  into one call means the UI's "Refresh" button is one await, not eight, and
  the eight underlying queries run in parallel rather than adding up their
  latencies.

**Where `AnalyticsService` is consumed (grep-verified):**

| Call site | What it does |
|---|---|
| `app/gradio/analytics_tab.py:24` | `_analytics_service = AnalyticsService()` — one module-level singleton, constructed at import time (safe because the constructor only builds repo client objects, it doesn't connect to Supabase) |
| `app/gradio/analytics_tab.py:123` | `_refresh()` calls `await _analytics_service.dashboard_summary()`, wired to the "Refresh" button (`app/gradio/analytics_tab.py:174`) |
| `tests/integration/test_services.py:192-234` | Constructs `AnalyticsService` with seven fake repos and asserts `dashboard_summary()`'s key set and `ai_query_stats()`'s averaging math |

No CLI/script entrypoint calls `AnalyticsService` — it is Gradio-only.

---

## 3. `app/services/chat_service.py`

Module docstring: "Thin orchestration layer between the Gradio chat UI and
`app.ai.Assistant` + the conversation repositories — the UI never touches
the AI/retrieval/DB layers directly." This is the architectural boundary the
whole file exists to enforce: `chat_tab.py` is only ever allowed to import
`ChatService`, never `Assistant`, `ConversationRepository`, or
`MessageRepository` directly (verified — see §7).

### Imports

```python
from app.ai.assistant import Assistant
from app.models.pydantic import ChatMessage, Conversation
from app.repositories.conversation_repository import ConversationRepository
from app.repositories.message_repository import MessageRepository
```

- `Assistant` — the actual LLM-calling class (`app/ai/assistant.py`).
  `ChatService` does not reimplement any AI logic; it purely forwards to
  `Assistant.ask`.
- `ChatMessage`, `Conversation` — Pydantic models used as return types.
- `ConversationRepository`, `MessageRepository` — the two tables this file
  reads directly for conversation-list/history/search/export features that
  `Assistant` itself doesn't expose.

### `class ChatService`

```python
def __init__(
    self,
    assistant: Assistant | None = None,
    conversation_repo: ConversationRepository | None = None,
    message_repo: MessageRepository | None = None,
) -> None:
    self.assistant = assistant or Assistant()
    self.conversation_repo = conversation_repo or ConversationRepository()
    self.message_repo = message_repo or MessageRepository()
```

Same injection pattern as `AnalyticsService`. Notably, `Assistant()`'s
constructor (`app/ai/assistant.py:94-103`) eagerly builds an
`AsyncOpenAI(api_key=settings.openai_api_key.get_secret_value())` client,
which raises if no `OPENAI_API_KEY` is configured. This is exactly why
`chat_tab.py` defers constructing `ChatService` until first use rather than
at import time (see `_get_chat_service`, §7) — instantiating `ChatService`
too early would make the whole Gradio Blocks graph fail to build in a bare
dev checkout.

**Methods:**

- **`ask(question: str, *, conversation_id: str | None = None) -> ChatMessage`**
  — pure passthrough to `self.assistant.ask(question, conversation_id=conversation_id)`
  (`app/ai/assistant.py:105-230`). `Assistant.ask` is where all the real work
  happens: it creates a new `Conversation` if `conversation_id` is `None`
  (`app/ai/assistant.py:118-120`), pulls up to 20 prior messages for context
  (`:122`), tries SQL generation (`app.ai.sql_generator.SQLGenerator`) and
  hybrid retrieval (`app.retrieval.RetrievalService.hybrid_search`)
  concurrently-in-spirit (each independently try/excepted so either can fail
  without aborting the turn, `:133-152`), builds a prompt from
  `app.prompts` templates with intent-keyword-based style selection
  (`_style_prompt_for`, `:54-67`), calls the OpenAI chat completion, and
  persists the user message, assistant message, a `QueryLog`, and an
  `AssistantLog` — every turn is fully logged regardless of whether SQL/retrieval
  succeeded. `ChatService.ask` exposes none of that machinery to the UI; it
  returns only the final `ChatMessage`.
- **`new_conversation(title: str | None = None) -> Conversation`** —
  `conversation_repo.create(Conversation(title=title))`. Not currently wired
  to any button (see §7 — "New chat" resets UI state only, it doesn't call
  this).
- **`list_conversations(*, limit: int = 50) -> list[Conversation]`** —
  `conversation_repo.list_all(order_by="updated_at", descending=True,
  limit=limit)`, i.e. `ConversationRepository`'s inherited `list_all` (no
  override needed), most-recently-active first. Wired to the sidebar
  "Refresh" button (`app/gradio/chat_tab.py:51-53`).
- **`search_conversations(query: str, *, limit: int = 20) -> list[Conversation]`**
  — `conversation_repo.search_by_title(query, limit=limit)`
  (`app/repositories/conversation_repository.py:19-33`), an `ilike
  "%query%"` search on `title`, restricted to non-deleted rows. Wired to the
  sidebar search box (`app/gradio/chat_tab.py:56-60`).
- **`get_history(conversation_id: str) -> list[ChatMessage]`** —
  `message_repo.by_conversation(conversation_id)`
  (`app/repositories/message_repository.py:11-18`), ordered oldest-first
  (`descending=False`) so a chat transcript reads top-to-bottom. Wired to
  selecting a conversation in the sidebar dropdown (`app/gradio/chat_tab.py:63-69`).
- **`clear_conversation(conversation_id: str) -> None`** —
  `conversation_repo.soft_delete(conversation_id)`
  (`app/repositories/base.py:187-188`), which sets `deleted_at` rather than
  issuing a `DELETE` — the conversation and its messages remain in the DB,
  just excluded from default list queries. Wired to the "Clear chat" button
  (`app/gradio/chat_tab.py:79-85`).
- **`export_conversation(conversation_id: str) -> str`** — the one method
  with real logic in this file rather than a pure delegate. Loads the
  conversation (`require_by_id`, which raises `RecordNotFoundError` instead
  of returning `None` — see `app/repositories/base.py:89-95` — if the id
  doesn't exist) and its full history, then builds a Markdown document by
  hand: an H1 of `conversation.display_title` (the `@computed_field` on
  `Conversation` that falls back to `"New conversation"` when `title` is
  `None`, `app/models/pydantic/conversation.py:27-30`), then for each message
  a `**You**`/`**Assistant**` header with an ISO timestamp, the message body,
  and — if `message.sources` is non-empty — an italic `Sources: ...` line.
  Wired to the "Export as Markdown" button (`app/gradio/chat_tab.py:153-168`).

**Where `ChatService` is consumed (grep-verified):**

| Call site | What it does |
|---|---|
| `app/gradio/chat_tab.py:21,42` | `_get_chat_service()` lazily constructs the module-level `ChatService` singleton on first call |
| `app/gradio/chat_tab.py:52,59,67,84,139,162` | every chat callback (`_refresh_conversations`, `_search_conversations`, `_load_conversation`, `_clear_chat`, `_ask`, `_export_conversation`) calls exactly one `ChatService` method |
| `tests/integration/test_services.py:242-297` | constructs `ChatService` with fake `Assistant`/repos and asserts `export_conversation` markdown shape and that `ask` delegates to `assistant.ask` with the right args |

No CLI/script entrypoint calls `ChatService` — it is Gradio-only (there is no
`scripts/ask.py` or similar).

---

## 4. `app/services/scrape_service.py`

Module docstring: "Orchestrates 'scrape a target, then ingest it' — the one
call site that bridges `app.apify` (scraping) and `app.ingestion`
(persistence), used by `scripts/run_scrape.py` and any future admin UI
action." That second clause is important and verified true today: **there is
currently no admin UI action** — `ScrapeService` is only ever invoked from
the CLI script and from tests (see the consumer table below). The Gradio
Analytics tab only *reads* `scrape_jobs` after the fact; it never starts one.

### Imports

```python
import asyncio
from dataclasses import dataclass
from typing import Literal

from app.apify import get_scraper
from app.config import get_settings
from app.ingestion.pipeline import IngestionPipeline, IngestionReport
from app.logging import get_logger
from app.models.pydantic.enums import PlatformName

ScrapeMode = Literal["profile", "posts", "comments", "hashtag", "keyword"]
```

- `get_scraper` — the registry lookup from `app/apify/__init__.py:32-37`.
  Given a `PlatformName | str`, it coerces to `PlatformName` and returns a
  freshly constructed, registered `BaseScraper` subclass instance (e.g.
  `InstagramScraper`), raising `UnsupportedPlatformError` if nothing is
  registered for that platform. The registry is populated by importing
  `app.apify.instagram/twitter/youtube` at the bottom of `app/apify/__init__.py:47`,
  each of which decorates its scraper class with `@register_scraper(...)` —
  this is the "single seam" the module docstring of `app/apify/__init__.py`
  describes, so adding a new platform never requires touching
  `ScrapeService`.
- `get_settings` — used once, to read `max_concurrent_scrapes` (default `5`,
  `app/config/settings.py:56`) for bounding `scrape_many`.
- `IngestionPipeline`, `IngestionReport` — `app/ingestion/pipeline.py`. This
  is the "then ingest it" half of the module docstring: every `scrape_*`
  method hands its raw `ScrapeResult` to `pipeline.ingest(...)`, which
  normalizes/dedupes/persists/embeds and returns an `IngestionReport`
  (dataclass of upsert counts + errors + `embeddings_generated`, defined at
  `app/ingestion/pipeline.py:44-71`).
- `Literal["profile", "posts", "comments", "hashtag", "keyword"]` as
  `ScrapeMode` — a closed string-literal type (not a full enum) used only as
  the type of `ScrapeTask.mode`; keeping it a `Literal` rather than a new
  `StrEnum` matches the five hardcoded method-name suffixes
  (`scrape_profile`, `scrape_posts`, ...) that `scrape_many` looks up via
  `getattr` (`:93` — see below), so the two must stay in lockstep.

### `@dataclass(slots=True, frozen=True) class ScrapeTask`

```python
platform: PlatformName | str
mode: ScrapeMode
target: str
limit: int = 50
```

"One unit of work for `scrape_many`." `frozen=True` + `slots=True` — same
rationale as `EmbeddableItem` in the embedding pipeline (per the reference
doc): these are constructed in bulk for a batch scrape and never mutated
afterward, so immutability + no `__dict__` overhead is a correctness/memory
win with no downside.

### `class ScrapeService`

```python
def __init__(
    self, pipeline: IngestionPipeline | None = None, *, max_concurrency: int | None = None
) -> None:
    self.pipeline = pipeline or IngestionPipeline()
    self._max_concurrency = max_concurrency or get_settings().max_concurrent_scrapes
```

Only one injectable dependency (`pipeline`) — `get_scraper` is a
module-level function, not a constructor param, so tests monkeypatch
`app.services.scrape_service.get_scraper` directly instead
(`tests/integration/test_services.py:53,128`) rather than passing a fake
scraper into the constructor.

**Methods** (all `async`, all return `IngestionReport`):

- **`scrape_profile(platform, identifier) -> IngestionReport`** —
  `get_scraper(platform).scrape_profile(identifier)` then
  `pipeline.ingest(result, platform=str(platform), job_type="profile",
  target=identifier)`.
- **`scrape_posts(platform, identifier, *, limit=50)`**,
  **`scrape_comments(platform, post_url_or_id, *, limit=100)`**,
  **`scrape_hashtag(platform, hashtag, *, limit=50)`**,
  **`scrape_keyword(platform, keyword, *, limit=50)`** — identical shape:
  call the matching `BaseScraper` method (`app/apify/base/scraper.py:67-82`;
  `scrape_hashtag`/`scrape_keyword` raise `NotImplementedError` by default
  and are only overridden where a platform's Apify actors actually support
  that search mode), then `pipeline.ingest(...)` with `job_type` set to the
  mode name and `target` set to whatever identifies the scrape (username,
  post id/URL, hashtag, or keyword).
- **`scrape_many(tasks: list[ScrapeTask]) -> list[IngestionReport]`** — runs
  every task through `asyncio.Semaphore(self._max_concurrency)` so a batch
  of, say, 50 targets doesn't open 50 simultaneous Apify actor runs at once
  (Apify actors are metered/rate-limited resources). Internally,
  `_run(task)` does `method = getattr(self, f"scrape_{task.mode}")` — this
  is exactly why `ScrapeMode`'s literal values must match the
  `scrape_<mode>` method names one-for-one — then calls it with `{}` extra
  kwargs for `"profile"` (which takes no `limit`) or `{"limit": task.limit}`
  otherwise, and gathers all task coroutines together.

**What it depends on (grep-verified call graph):**

```
ScrapeService.scrape_*
   |-- app.apify.get_scraper(platform)         (app/apify/__init__.py:32)
   |     -> BaseScraper subclass (Instagram/Twitter/YouTube)
   |-- scraper.scrape_profile/posts/comments/hashtag/keyword(...)
   |     -> ScrapeResult  (app/apify/base/scraper.py:24-50)
   `-- self.pipeline.ingest(result, platform=, job_type=, target=)
         -> app/ingestion/pipeline.py:118-140 (IngestionPipeline.ingest)
              -> dedupe, bulk_upsert authors/channels/posts/videos/comments,
                 media/hashtags/mentions/engagement ingestion,
                 then _generate_embeddings -> EmbeddingService.embed_batch
              -> returns IngestionReport
```

**Where `ScrapeService` is consumed (grep-verified):**

| Call site | What it does |
|---|---|
| `scripts/run_scrape.py:23,52` | The CLI entrypoint: `service = ScrapeService()`; `method = getattr(service, _MODES[mode])`; prints the resulting `IngestionReport`'s counters. This is the *only* production entrypoint that actually triggers a scrape. |
| `tests/integration/test_services.py:14,20,52-146` | Exercises every `scrape_*` method and `scrape_many` against a `FakeScraper` + fake pipeline, asserting the scraper is called with the right args and the pipeline's `ingest` receives the right `platform`/`job_type`/`target` kwargs |

**Not consumed by:** `app/gradio/*`. Neither `chat_tab.py` nor
`analytics_tab.py` imports `ScrapeService` or `ScrapeTask` — confirmed by
grepping `app/gradio/` for `scrape`/`Scrape` (§8 has the one-line grep
result: the only hits are display columns for *already-existing* job rows,
not a trigger to create one). Running a scrape today is a `python
scripts/run_scrape.py <platform> <mode> <target>` operation, not a Gradio
button click.

---

## 5. `app/gradio/__init__.py`

```python
from app.gradio.app import build_app, main

__all__ = ["build_app", "main"]
```

Re-exports the two public entrypoints of `app/gradio/app.py` so callers can
write `from app.gradio import main` instead of `from app.gradio.app import
main`. Functionally identical in spirit to `app/services/__init__.py` (§1).

**Where used:** `scripts/launch_gradio.py:14` imports `main` from
`app.gradio.app` directly (not through this `__init__.py`), and
`tests/unit/test_gradio_app.py:34` imports `build_app` from `app.gradio.app`
directly too. As with §1, nothing in the current codebase actually imports
through the package root — this file is present for API ergonomics rather
than because some existing caller needs it.

---

## 6. `app/gradio/app.py`

Module docstring: "Entry point for the Gradio UI: wires the Chat and
Analytics tabs into a single `gr.Blocks` app. Kept deliberately thin —
`build_app()` only lays out tabs; all business logic lives in
`app.services` and is invoked from the tab-specific callback modules." This
is the file that answers assignment point 3 ("how does app.py assemble the
tabs"), so it's covered in full here.

### Imports

```python
import gradio as gr
from app.gradio.analytics_tab import build_analytics_tab
from app.gradio.chat_tab import build_chat_tab
```

Only the two tab-builder functions — no service, repository, or AI import in
this file at all. That's deliberate: `app.py` never needs credentials to be
configured, because it never constructs anything that talks to
Supabase/OpenAI; it only calls two functions that lay out Gradio components
and register callbacks (which *do* eventually touch those backends, but only
when clicked, not when built).

### `build_app() -> gr.Blocks`

```python
with gr.Blocks(title="Social Media Intelligence Platform") as blocks, gr.Tabs():
    with gr.Tab("Chat"):
        build_chat_tab()
    with gr.Tab("Analytics"):
        build_analytics_tab()
return blocks
```

- Opens one `gr.Blocks` context (the root of the whole component tree) and
  one `gr.Tabs()` container inside it, using Python's combined `with A, B:`
  syntax to open both context managers in one statement.
- Two `gr.Tab(...)` children, each simply calling the corresponding
  `build_*_tab()` function *while the tab's context manager is open* — this
  is how Gradio decides which components belong to which tab: it's purely
  about what's constructed inside the `with gr.Tab(...):` block, not an
  explicit "add to tab" API call. `build_chat_tab()`/`build_analytics_tab()`
  return `None`; their job is entirely the side effect of instantiating
  components and wiring `.click()`/`.submit()`/`.change()` handlers while
  that Gradio context is active. This is why their docstrings warn "Must be
  called inside an open `gr.Blocks()`" (`app/gradio/chat_tab.py:172`,
  `app/gradio/analytics_tab.py:143`) — calling them outside a `Blocks`
  context would attach components to no graph at all.
- The docstring explains a specific version quirk: `theme` is passed to
  `.launch()` (in `main()`) rather than to the `gr.Blocks(...)` constructor,
  because this Gradio version still accepts `theme` on the constructor for
  backwards compatibility but emits a `UserWarning` telling you to move it —
  so the code follows the warning's advice rather than silencing it.
- `build_app()` is safe to call with zero Supabase/OpenAI credentials
  configured (verified by `tests/unit/test_gradio_app.py:45-62`, which calls
  it twice in the same process with no env vars set and asserts both calls
  return a `gr.Blocks` instance) — because, transitively, neither
  `build_chat_tab` nor `build_analytics_tab` constructs a credential-needing
  object at build time (see §7/§8: `ChatService` construction is deferred to
  first click; `AnalyticsService()` is constructed eagerly at
  `analytics_tab.py` import time, but its constructor only builds
  `BaseRepository` wrapper objects, which don't open a network connection
  until a query actually runs).

### `main() -> None`

```python
def main() -> None:
    build_app().launch(theme=gr.themes.Soft())

if __name__ == "__main__":
    main()
```

Builds the app and calls Gradio's blocking `.launch()`, which starts the
local web server. The inline comment explains why `server_name`/`server_port`
are left at their defaults (`None`): Gradio resolves them from the
`GRADIO_SERVER_NAME`/`GRADIO_SERVER_PORT` environment variables at launch
time, falling back to `127.0.0.1:7860`; the Dockerfile sets
`GRADIO_SERVER_NAME=0.0.0.0` so the container is reachable from outside
without hardcoding a bind address in source that would otherwise also affect
local (non-container) runs.

**Where used:** `scripts/launch_gradio.py:14-17` is the only production
entrypoint — it inserts the repo root onto `sys.path` and calls
`app.gradio.app.main()` directly under `if __name__ == "__main__":`. This is
the script a developer/operator actually runs to bring up the UI.
`tests/unit/test_gradio_app.py` calls `build_app()` (never `main()`, since
that would block on `.launch()`).

### The full "user asks a question" flow, traced end to end

This is the concrete trace assignment point 3 asks for, for the one
actually-wired "ask a question" action (there is no wired "run a scrape"
action in the UI — see §4's finding):

```
1. Browser: user types a question, clicks "Send" (or presses Enter)
     app/gradio/chat_tab.py:208-211
       send_btn.click(_append_user_message, ...).then(_ask, ...)

2. _append_user_message(message, history)        chat_tab.py:88-96
     - echoes the user's turn into the visible gr.Chatbot immediately
     - clears the textbox (unless the message was blank/whitespace)

3. _ask(history, conversation_id)                  chat_tab.py:120-150
     - yields a "_Thinking..._" placeholder turn first (perceived latency)
     - awaits _get_chat_service().ask(question, conversation_id=...)

4. ChatService.ask(question, conversation_id=...)  chat_service.py:25-26
     - pure passthrough to self.assistant.ask(...)

5. Assistant.ask(question, conversation_id=...)    app/ai/assistant.py:105-230
     - creates a Conversation row if conversation_id is None
       (ConversationRepository.create)
     - loads up to 20 prior ChatMessage rows for context
       (MessageRepository.by_conversation)
     - persists the user's ChatMessage
     - tries SQLGenerator.generate_and_execute(question, history_text)
       (app/ai/sql_generator.py) -- best-effort, caught on failure
     - tries RetrievalService.hybrid_search(question, filters, limit=8)
       (app/retrieval/service.py) -- best-effort, caught on failure
         - hybrid_search runs keyword_search (Postgres full-text on
           documents.search_vector) and semantic_search (embeds the query,
           calls EmbeddingRepository.match -> match_embeddings RPC,
           pgvector cosine similarity) concurrently, merges + reweights
     - builds a prompt (ASSISTANT_SYSTEM_PROMPT + optional intent-matched
       style prompt + context + question) and calls
       AsyncOpenAI.chat.completions.create(...)
     - persists the assistant ChatMessage, a QueryLog, and an AssistantLog
     - returns the assistant ChatMessage

6. Back in _ask: yields (history + assistant turn, reply.conversation_id)
     chat_tab.py:149-150
     -> re-renders gr.Chatbot with the final answer, and gr.State
        conversation_id is updated so the *next* question in this browser
        session continues the same conversation.
```

Every failure point in step 5 (SQL generation, retrieval, the OpenAI call
itself) is independently caught so a partial failure degrades the answer's
quality rather than crashing the turn; `_ask` (step 3) additionally wraps
the whole `ChatService.ask` call in a `try/except` so *any* unexpected
exception is shown to the user as an inline "Sorry, something went wrong"
message rather than a stack trace (`chat_tab.py:138-147`).

The **analogous "click Refresh" flow** for Analytics is much shorter because
it's read-only: `refresh_btn.click(_refresh, ...)` (`analytics_tab.py:174`)
→ `_refresh()` (`analytics_tab.py:116-139`) → `AnalyticsService.dashboard_summary()`
(§2) → eight concurrent repository reads → eight/nine
`gr.update`-friendly return values that populate the number tiles, bar plot,
and four dataframes in one shot.

---

## 7. `app/gradio/chat_tab.py`

Module docstring states the same boundary `ChatService` itself documents:
"every question, history load, search, and export goes through
`ChatService` so this layer never touches the AI/retrieval/DB layers
directly." It also states the state-management rule: anything that must
survive between callbacks (the active `conversation_id`) lives in a
`gr.State`, never a module global, "since Gradio serves every browser
session from the same Python process" — a module global would leak one
user's active conversation into every other concurrent user's session.

### Imports

```python
import tempfile
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import gradio as gr
from app.logging import get_logger
from app.models.pydantic import Conversation
from app.services.chat_service import ChatService
```

- `tempfile`, `Path` — used only by `_export_conversation` to write the
  Markdown export to a real file on disk (Gradio's `gr.File` output
  component needs an actual filesystem path to serve for download, not an
  in-memory string).
- `AsyncIterator` — the return-type annotation for `_ask`, which is an async
  generator (`yield`s twice) rather than a plain coroutine, because it needs
  to push an interim "thinking" state to the UI before the real answer is
  ready (Gradio supports this by letting an event handler be a generator
  function; each `yield` pushes a UI update).
- `Conversation` — used only as a type in `_conversation_choices`'s
  parameter.
- `ChatService` — the one business-logic dependency this whole module is
  allowed to import, per the docstring's stated boundary.

### Module-level state

```python
_chat_service: ChatService | None = None

ChatHistory = list[dict[str, str]]
```

- `_chat_service` starts `None` and is filled in lazily by
  `_get_chat_service()` (below) — **not** constructed at import time, unlike
  `analytics_tab.py`'s `_analytics_service` (§8). The comment explains why:
  `ChatService()`'s default construction builds an `Assistant()`, which
  eagerly builds an `AsyncOpenAI` client and raises if `OPENAI_API_KEY`
  isn't set (`app/ai/assistant.py:98`) — deferring construction means the
  Blocks graph can still be built (`build_app()`, imported by tests and
  `scripts/launch_gradio.py`) in a bare dev checkout with no API key; only an
  actual "Send" click needs credentials.
- `ChatHistory` — a type alias documenting the exact shape `gr.Chatbot`
  requires in this Gradio version: a list of `{"role": ..., "content":
  ...}` dicts (the comment notes the older tuple-pairs format "no longer
  exists").

### Functions

- **`_get_chat_service() -> ChatService`** — classic lazy-singleton via
  `global`. Every other function in this file that needs the service calls
  this rather than touching `_chat_service` directly.
- **`_conversation_choices(conversations: list[Conversation]) -> list[tuple[str, str]]`**
  — maps each `Conversation` to a `(label, value)` pair for `gr.Dropdown`:
  label is `c.display_title` (falls back to `"New conversation"` when
  `title` is `None`), value is `str(c.id)` (the dropdown's underlying value
  must be a plain string/hashable, not a `uuid.UUID` object, for Gradio's
  choice-matching to work reliably). Pure function — no I/O — and directly
  unit-tested (`tests/unit/test_gradio_app.py:70-85`).
- **`_refresh_conversations() -> gr.Dropdown`** (async) — calls
  `ChatService.list_conversations()` and returns a **new** `gr.Dropdown(...)`
  instance (not a plain list) with `value=None` — returning a component
  instance rather than a raw value is Gradio's mechanism for updating both a
  component's `choices` *and* resetting its selected `value` in one event
  return. Wired to the "Refresh" button (`chat_tab.py:218`).
- **`_search_conversations(query: str) -> gr.Dropdown`** (async) — if
  `query` is blank, delegates to `_refresh_conversations()` (so clearing the
  search box repopulates the full recent list rather than showing an empty
  dropdown); otherwise calls `ChatService.search_conversations(query)`.
  Wired to the search textbox's `submit` event (`chat_tab.py:219`).
- **`_load_conversation(conversation_id: str | None) -> tuple[ChatHistory, str | None]`**
  (async) — if no id, returns `([], None)` (blank slate). Otherwise calls
  `ChatService.get_history(conversation_id)` and maps each `ChatMessage` to
  the `{"role", "content"}` shape `gr.Chatbot` needs. Wired to the sidebar
  dropdown's `change` event (`chat_tab.py:220-222`) — selecting a
  conversation loads its transcript.
- **`_new_chat() -> tuple[ChatHistory, None, gr.Dropdown]`** — synchronous,
  no I/O at all. Returns an empty history, `None` conversation id, and a
  reset dropdown selection. The docstring is explicit that this is purely
  local UI state reset — it does **not** call `ChatService.new_conversation`
  — because `ChatService.ask` lazily creates the actual DB `Conversation`
  row on the first real question (`Assistant.ask`, `app/ai/assistant.py:118-120`).
  This means `ChatService.new_conversation` (§3) is currently **dead code**
  from the UI's perspective — grep confirms no `gradio` file calls it.
  Wired to the "New chat" button (`chat_tab.py:213-215`).
- **`_clear_chat(conversation_id) -> tuple[ChatHistory, None]`** (async) —
  if a conversation is active, calls `ChatService.clear_conversation(...)`
  (soft-delete), then always returns a blank chat. Wired to "Clear chat"
  (`chat_tab.py:216`).
- **`_append_user_message(message: str, history: ChatHistory) -> tuple[str, ChatHistory]`**
  — synchronous, pure. If `message` is blank/whitespace, returns it and
  `history` unchanged (so a stray Enter press or accidental blank submit is
  a no-op that doesn't clear what the user typed). Otherwise returns `("",
  [*history, {"role": "user", ...}])` — clearing the textbox and appending
  the user's turn. This is the *first* half of the two-stage `.then()` chain
  described in `build_chat_tab` below; it exists as its own function
  (instead of being inlined into `_ask`) so the user's message appears in
  the chat log **immediately**, before the (potentially multi-second)
  assistant call even starts.
- **`_extract_text(content: Any) -> str`** — normalizes a chat message's
  `content` field to a plain string. Handles four shapes: `str` (pass
  through), `dict` (pull `"text"` key, default `""`), `list` (join each
  part's `"text"` if it's a dict, else `str(part)`), and any other type
  (`str(content)`). The docstring explains exactly why this exists:
  `_append_user_message` always produces a plain string internally, **but**
  Gradio auto-generates a `/call/_ask` HTTP API endpoint for every wired
  function, and a direct API caller can send a multimodal-shaped payload
  (`[{"text": "...", "type": "text"}]`) instead — without this
  normalization, that shape would fail `Conversation`/message validation and
  crash the turn instead of degrading gracefully. Confirmed as a
  real-world-motivated fix by the test comment at
  `tests/unit/test_gradio_app.py:121-124`: "This is the real payload shape
  that crashed `_ask` in production."
- **`_ask(history, conversation_id) -> AsyncIterator[tuple[ChatHistory, str | None]]`**
  — an async generator (note `async def` + `yield`, not `return`). Guards:
  if `history` is empty or its last entry isn't a `"user"` turn, returns
  immediately (nothing to answer). Otherwise: extracts the question via
  `_extract_text`, yields a `"_Thinking..._"` placeholder turn immediately,
  then awaits `ChatService.ask(...)`. On any exception, logs it
  (`logger.exception`) and yields a friendly inline error turn instead of
  letting the exception propagate to Gradio's default error UI. On success,
  yields the final history with the assistant's real answer and the
  (possibly newly created) `conversation_id` from the reply.
- **`_export_conversation(conversation_id) -> gr.File | None`** (async) — if
  no active conversation, calls `gr.Warning(...)` (a toast notification) and
  returns `None`. Otherwise calls `ChatService.export_conversation(...)`,
  writes the Markdown to a `tempfile.NamedTemporaryFile` (not a hardcoded
  path — this must work regardless of OS/working directory and regardless
  of how many concurrent users export at once), and returns a
  `gr.File(value=..., visible=True)` so the previously-hidden download
  component becomes visible with the new file attached.

### `build_chat_tab() -> None`

Lays out (in order): a `gr.State(value=None)` for `conversation_id`; a
`gr.Sidebar` containing a search textbox, a conversation-picker dropdown, a
"Refresh" button, an "Export as Markdown" button, and a hidden `gr.File`;
and a main column containing a `gr.Chatbot`, a message textbox + "Send"
button row, and a "New chat"/"Clear chat" button row.

**Event wiring**, all grep-verified against the actual `build_chat_tab` body:

| Trigger | Handler chain | Inputs → Outputs |
|---|---|---|
| `message_box.submit` **and** `send_btn.click` (looped over both, `chat_tab.py:208-211`) | `_append_user_message` **then** (`.then(...)`) `_ask` | `[message_box, chatbot] -> [message_box, chatbot]`, then `[chatbot, conversation_id] -> [chatbot, conversation_id]` |
| `new_chat_btn.click` | `_new_chat` | `None -> [chatbot, conversation_id, conversation_picker]` |
| `clear_chat_btn.click` | `_clear_chat` | `[conversation_id] -> [chatbot, conversation_id]` |
| `refresh_btn.click` | `_refresh_conversations` | `None -> [conversation_picker]` |
| `search_box.submit` | `_search_conversations` | `[search_box] -> [conversation_picker]` |
| `conversation_picker.change` | `_load_conversation` | `[conversation_picker] -> [chatbot, conversation_id]` |
| `export_btn.click` | `_export_conversation` | `[conversation_id] -> [export_file]` |

The `.then(...)` chaining on the Send/submit path is the key design point:
Gradio runs `_append_user_message` to completion (updating `message_box` and
`chatbot`) and only *then* starts `_ask` using the just-updated `chatbot`
value as input — guaranteeing the user's own message is visible before the
(slower, network-bound) assistant call begins.

**Where used:** `app/gradio/app.py:13,28` — `build_chat_tab()` is called
once, inside the `"Chat"` tab of `build_app()`. Also imported (its pure
helpers only) by `tests/unit/test_gradio_app.py:35-40` for direct unit
testing of `_conversation_choices`, `_append_user_message`, `_extract_text`,
and `_new_chat`.

---

## 8. `app/gradio/analytics_tab.py`

Module docstring: "a read-only dashboard over `AnalyticsService`.
Everything here is populated on demand by the 'Refresh' button rather than
at Blocks-build time" — reiterating the same "must build without
credentials" constraint as `app.py`, but noting the mechanism is slightly
different from `chat_tab.py`: here the **service instance** is constructed
eagerly at import time (see below), it's only the *data fetch* that's
deferred to the button click.

### Imports

```python
from typing import Any

import pandas as pd

import gradio as gr
from app.logging import get_logger
from app.services.analytics_service import AnalyticsService
```

`pandas` is imported because every tabular Gradio output in this tab
(`gr.Dataframe`) is fed a `pd.DataFrame`, and the one chart
(`gr.BarPlot`) is also fed a `DataFrame` with named `x`/`y` columns.

### Module-level state

```python
_analytics_service = AnalyticsService()
```

Unlike `chat_tab.py`'s `_chat_service` (deferred), this is constructed
**immediately at import time**. The comment says this is safe "for the same
reasoning" as chat's lazy pattern — repo clients are cheap to hold — but the
practical difference is that `AnalyticsService()`'s constructor (§2) only
ever default-constructs seven `BaseRepository` subclasses, none of which
open a network connection or read an API key eagerly in their `__init__`
(unlike `Assistant()`, which does read/validate `OPENAI_API_KEY`
immediately). That asymmetry — `Assistant()`'s constructor validates
credentials, plain repository constructors don't — is exactly why one
service can be built eagerly at import time and the other can't.

```python
_HASHTAGS_COLUMNS = ["tag", "id"]
_AUTHORS_COLUMNS = ["username", "platform", "follower_count"]
_ENGAGEMENT_COLUMNS = ["post_id", "likes", "views", "total_engagement"]
_JOBS_COLUMNS = ["platform", "job_type", "status", "target", "records_scraped", "created_at"]
```

Fixed column-name lists, one per dashboard table, used by the frame-builder
functions below to guarantee a consistent schema regardless of whether data
is present.

### Frame-builder functions (all pure, no I/O — directly unit-tested)

- **`_records_frame(records: list[dict], columns: list[str]) -> pd.DataFrame`**
  — the shared primitive every other `_*_frame` function calls. If
  `records` is empty, returns `pd.DataFrame(columns=columns)` rather than
  `pd.DataFrame([])`; the docstring/comment explains why this distinction
  matters: an empty `pd.DataFrame([])` has **no columns at all**, which
  renders in `gr.Dataframe` as a table with no headers — confusing for a
  user looking at "zero hashtags trending" vs. "the table is broken."
  Otherwise builds `pd.DataFrame(records)[columns]`, which both selects and
  **orders** the columns (any extra keys in `records` are dropped).
- **`_hashtags_frame(hashtags: list[dict]) -> pd.DataFrame`** — thin
  wrapper: `_records_frame(hashtags, _HASHTAGS_COLUMNS)`. Note the input is
  already `dict`s (not a Pydantic model) because
  `AnalyticsService.trending_hashtags` returns raw dicts (§2 — the
  underlying `HashtagRepository.trending` selects only `id, tag` columns
  directly from Supabase).
- **`_authors_frame(authors: list[Any]) -> pd.DataFrame`** — maps each
  `Author` object to a `{"username", "platform", "follower_count"}` dict
  first, then calls `_records_frame`. Needs this extra mapping step (unlike
  `_hashtags_frame`) because its input is real `Author` model instances, not
  pre-shaped dicts.
- **`_engagement_frame(posts: list[Any]) -> pd.DataFrame`** — same pattern
  for `Engagement` objects → `{"post_id", "likes", "views",
  "total_engagement"}`.
- **`_jobs_frame(jobs: list[Any]) -> pd.DataFrame`** — same pattern for
  `ScrapeJob` objects → the six `_JOBS_COLUMNS` fields. This is the only
  place in the entire UI that surfaces a `ScrapeJob`'s `status` — confirming
  §4's finding that the UI can *observe* scrape jobs but never *creates*
  one.
- **`_platform_frame(distribution: dict[str, int]) -> pd.DataFrame`** —
  simplest one: builds a two-column frame directly from a `{platform:
  count}` dict's keys/values, for the `gr.BarPlot`.
- **`_ai_stats_markdown(stats: dict[str, Any]) -> str`** — formats
  `AnalyticsService.ai_query_stats()`'s two-key dict into one Markdown
  sentence, rendering `avg_latency_ms` as `"n/a"` rather than `"None ms"`
  when there's no latency data yet.

### `_EMPTY_RESULT` and `_refresh()`

```python
_EMPTY_RESULT: tuple[Any, ...] = (
    gr.update(visible=False), 0, 0, _platform_frame({}), _hashtags_frame([]),
    _authors_frame([]), _engagement_frame([]), _jobs_frame([]), _ai_stats_markdown({}),
)
```

A module-level constant giving the "nothing loaded yet / failed" shape for
every one of the nine outputs, computed once at import time (cheap — all
inputs are empty). Used only for its **tail** (`_EMPTY_RESULT[1:]`, skipping
the banner slot) inside the error branch of `_refresh`.

```python
async def _refresh() -> tuple[Any, ...]:
    try:
        summary = await _analytics_service.dashboard_summary()
    except Exception as exc:
        logger.exception("dashboard_summary failed")
        banner = gr.update(value=f"Could not load analytics: {exc}", visible=True)
        return (banner, *_EMPTY_RESULT[1:])
    return (
        gr.update(visible=False),
        summary["total_posts"], summary["total_comments"],
        _platform_frame(summary["platform_distribution"]),
        _hashtags_frame(summary["trending_hashtags"]),
        _authors_frame(summary["most_active_authors"]),
        _engagement_frame(summary["top_engagement_posts"]),
        _jobs_frame(summary["recent_scrape_jobs"]),
        _ai_stats_markdown(summary["ai_query_stats"]),
    )
```

The single callback behind the "Refresh" button. On success, hides the
status banner (`gr.update(visible=False)`) and returns the nine populated
outputs built from `AnalyticsService.dashboard_summary()`'s dict (§2). On
**any** exception (e.g. no Supabase credentials configured — explicitly
called out as "the default in this dev environment" in the docstring), logs
the full traceback server-side but shows the user a friendly banner with the
exception text and empty tiles/tables — never a raw traceback in the
browser.

### `build_analytics_tab() -> None`

Lays out: a hidden `gr.Markdown` status banner, a "Refresh" button, a row of
two `gr.Number` tiles (total posts/comments), a `gr.BarPlot` (`x="platform",
y="count"`), a row of two `gr.Dataframe`s (hashtags, authors), a second row
of two more `gr.Dataframe`s (engagement, jobs), and a closing `gr.Markdown`
for the AI stats sentence. All nine components are collected into one
`outputs` list, and:

```python
refresh_btn.click(_refresh, inputs=None, outputs=outputs)
```

is the **only** event binding in this entire module — one button, one
handler, nine outputs, no inputs (the dashboard always shows "everything,"
there's no filter/parameter UI here).

**Where used:** `app/gradio/app.py:12,29` — `build_analytics_tab()` is
called once, inside the `"Analytics"` tab of `build_app()`. Its pure
frame-builder helpers are also imported directly by
`tests/unit/test_gradio_app.py:25-33` for unit testing without needing a
running Blocks app or a real database.

---

## 9. Summary — how the pieces fit together

- **Two independent UI surfaces**, assembled by `build_app()`
  (`app/gradio/app.py:16-31`) into one `gr.Blocks` with a `gr.Tabs()`: Chat
  (`chat_tab.py`) and Analytics (`analytics_tab.py`). Neither tab module
  imports the other, and neither imports a repository, `app.ai`, or
  `app.apify` directly — every DB/AI/scrape access is mediated by exactly
  one service class per tab (`ChatService` for Chat, `AnalyticsService` for
  Analytics), matching the boundary both service modules' docstrings state.
- **Two different laziness strategies**, driven by whether a service's
  constructor can fail without credentials: `ChatService` (via
  `Assistant()`, which reads `OPENAI_API_KEY` at construction time) is
  built lazily on first use (`chat_tab.py`'s `_get_chat_service`);
  `AnalyticsService` (whose dependencies are plain repository wrappers with
  no eager credential check) is built once at import time
  (`analytics_tab.py`'s `_analytics_service = AnalyticsService()`).
- **`ScrapeService` is the odd one out**: it is fully built and tested
  (`app/services/scrape_service.py`, `tests/integration/test_services.py`),
  its module docstring even anticipates "any future admin UI action" using
  it — but as of this codebase, the Gradio UI never imports or calls it.
  The only production caller is the CLI script `scripts/run_scrape.py`. The
  Analytics tab shows the *results* of past scrape jobs
  (`ScrapeJobRepository.recent`, surfaced through
  `AnalyticsService.recent_scrape_jobs`) but has no button that starts a new
  one — "run a scrape" today is a terminal command, not a UI action.
- **The one real, fully-wired UI → DB round trip is asking a question**:
  Send/Enter in the Chat tab → `ChatService.ask` → `Assistant.ask` → SQL
  generation + hybrid (keyword + semantic/pgvector) retrieval + an OpenAI
  chat completion → persisted `ChatMessage`/`QueryLog`/`AssistantLog` rows →
  rendered back into `gr.Chatbot`. The Analytics tab's "Refresh" is the
  other fully-wired round trip, but strictly read-only: eight concurrent
  repository queries bundled by `AnalyticsService.dashboard_summary`.
