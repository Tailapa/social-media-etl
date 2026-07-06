# API Documentation

This project has **no HTTP/REST API layer**. `fastapi` and `uvicorn` appear in `requirements.txt`
but nothing in `app/` constructs a `FastAPI()` app, defines a route, or runs `uvicorn` — they are
currently unused dependencies, reserved (per the original project spec) for an optional future
HTTP API that has not been built yet. Do not assume any of the endpoints below exist; there are
none.

What follows instead is the **internal Python API**: the classes and methods a developer
extending this project, or scripting against it directly (as `scripts/run_scrape.py` and
`app/gradio/*` already do), would call. All methods are `async def` unless noted otherwise, and all
examples assume `asyncio.run(...)` or an existing event loop.

## `ScrapeService` (`app/services/scrape_service.py`)

Orchestrates "scrape a target, then ingest it" — the one call site bridging `app.apify` (scraping)
and `app.ingestion` (persistence).

```python
class ScrapeService:
    def __init__(self, pipeline: IngestionPipeline | None = None) -> None: ...

    async def scrape_profile(self, platform: PlatformName | str, identifier: str) -> IngestionReport
    async def scrape_posts(self, platform: PlatformName | str, identifier: str, *, limit: int = 50) -> IngestionReport
    async def scrape_comments(self, platform: PlatformName | str, post_url_or_id: str, *, limit: int = 100) -> IngestionReport
    async def scrape_hashtag(self, platform: PlatformName | str, hashtag: str, *, limit: int = 50) -> IngestionReport
    async def scrape_keyword(self, platform: PlatformName | str, keyword: str, *, limit: int = 50) -> IngestionReport
```

Each method resolves the platform's scraper via `app.apify.get_scraper(platform)`, calls the
matching `BaseScraper` method, and passes the resulting `ScrapeResult` to
`IngestionPipeline.ingest(..., job_type=<mode>, target=<identifier>)`. All five methods return an
`IngestionReport` (see below). `scrape_hashtag`/`scrape_keyword` raise `NotImplementedError` if the
underlying scraper doesn't support that mode (YouTube supports neither).

```python
from app.services.scrape_service import ScrapeService

service = ScrapeService()
report = await service.scrape_posts("instagram", "nasa", limit=25)
print(report.posts_upserted, report.embeddings_generated, report.errors)
```

## `IngestionPipeline.ingest` (`app/ingestion/pipeline.py`)

```python
class IngestionPipeline:
    def __init__(self, *, author_repo=None, channel_repo=None, video_repo=None, post_repo=None,
                 comment_repo=None, media_repo=None, hashtag_repo=None, post_hashtag_repo=None,
                 mention_repo=None, engagement_repo=None, scrape_job_repo=None,
                 embedding_service=None) -> None: ...

    async def ingest(
        self,
        result: ScrapeResult,
        *,
        platform: str,
        job_type: str,
        target: str | None = None,
    ) -> IngestionReport
```

Takes one `ScrapeResult` (posts/authors/comments/media/channels/videos + `raw_item_count`) and
runs it through: create a `scrape_jobs` row (status `running`) -> dedupe every entity list by
`dedup_key` -> bulk-upsert authors -> remap and bulk-upsert channels -> remap and bulk-upsert posts
-> remap and bulk-upsert videos -> remap and bulk-upsert comments -> relink comment parent ids ->
ingest media/hashtags/mentions/engagement -> generate embeddings for post captions, comment text,
and video transcripts -> mark the job `succeeded` (no errors) or `partial` (some steps failed but
the run completed) or `failed` (an unhandled exception aborted the run). Every per-entity step
isolates its own failures into `IngestionReport.errors` rather than raising, except a genuinely
fatal exception in `_run` itself, which is caught once at the top of `ingest()` and marks the job
`failed`.

Returns an `IngestionReport` dataclass:

```python
@dataclass(slots=True)
class IngestionReport:
    job_id: str | None
    authors_upserted: int
    channels_upserted: int
    posts_upserted: int
    videos_upserted: int
    comments_upserted: int
    media_created: int
    hashtags_linked: int
    mentions_created: int
    engagement_upserted: int
    embeddings_generated: int
    errors: list[str]

    @property
    def total_records(self) -> int  # sum of authors/channels/posts/videos/comments upserted
```

```python
from app.ingestion.pipeline import IngestionPipeline

pipeline = IngestionPipeline()
report = await pipeline.ingest(scrape_result, platform="twitter", job_type="hashtag", target="ai")
if report.errors:
    print(f"{len(report.errors)} step(s) failed but the run completed:", report.errors)
```

## `ChatService` (`app/services/chat_service.py`)

Thin orchestration layer between the Gradio chat UI and `app.ai.Assistant` plus the conversation
repositories — nothing above this layer touches the AI/retrieval/DB layers directly.

```python
class ChatService:
    def __init__(self, assistant: Assistant | None = None,
                 conversation_repo: ConversationRepository | None = None,
                 message_repo: MessageRepository | None = None) -> None: ...

    async def ask(self, question: str, *, conversation_id: str | None = None) -> ChatMessage
    async def new_conversation(self, title: str | None = None) -> Conversation
    async def list_conversations(self, *, limit: int = 50) -> list[Conversation]
    async def search_conversations(self, query: str, *, limit: int = 20) -> list[Conversation]
    async def get_history(self, conversation_id: str) -> list[ChatMessage]
    async def clear_conversation(self, conversation_id: str) -> None
    async def export_conversation(self, conversation_id: str) -> str
```

- `ask` delegates directly to `Assistant.ask` and returns the persisted assistant `ChatMessage`.
- `list_conversations`/`search_conversations` back the Gradio sidebar's dropdown and search box.
- `clear_conversation` soft-deletes the conversation (`ConversationRepository.soft_delete`) —
  the row and its messages remain in the database (`deleted_at` set) rather than being removed.
- `export_conversation` renders the full conversation as a Markdown string (speaker labels,
  timestamps, cited sources) for the Gradio "Export as Markdown" button.

```python
from app.services.chat_service import ChatService

chat = ChatService()
conversation = await chat.new_conversation(title="Instagram Q3 review")
reply = await chat.ask("What were the most liked posts last week?", conversation_id=str(conversation.id))
print(reply.content, reply.sources)
```

## `AnalyticsService` (`app/services/analytics_service.py`)

Read-only aggregation queries backing the Gradio analytics dashboard.

```python
class AnalyticsService:
    def __init__(self, post_repo=None, comment_repo=None, author_repo=None, engagement_repo=None,
                 hashtag_repo=None, scrape_job_repo=None, query_log_repo=None) -> None: ...

    async def total_posts(self) -> int
    async def total_comments(self) -> int
    async def platform_distribution(self) -> dict[str, int]
    async def most_active_authors(self, *, limit: int = 10) -> list[Author]
    async def trending_hashtags(self, *, limit: int = 10) -> list[dict[str, Any]]
    async def top_engagement_posts(self, *, limit: int = 10) -> list[Engagement]
    async def recent_scrape_jobs(self, *, limit: int = 20) -> list[ScrapeJob]
    async def ai_query_stats(self, *, limit: int = 200) -> dict[str, Any]
    async def dashboard_summary(self) -> dict[str, Any]
```

`dashboard_summary()` is the single call the Gradio analytics tab makes on "Refresh" — it runs all
of the above concurrently via `asyncio.gather` and returns one dict keyed
`total_posts`/`total_comments`/`platform_distribution`/`most_active_authors`/`trending_hashtags`/
`top_engagement_posts`/`recent_scrape_jobs`/`ai_query_stats`.

```python
from app.services.analytics_service import AnalyticsService

analytics = AnalyticsService()
summary = await analytics.dashboard_summary()
print(summary["platform_distribution"])  # {"instagram": 120, "twitter": 45, "youtube": 12, ...}
```

## `Assistant.ask` (`app/ai/assistant.py`)

```python
class Assistant:
    def __init__(self, retrieval: RetrievalService | None = None,
                 sql_generator: SQLGenerator | None = None, client: AsyncOpenAI | None = None,
                 model: str | None = None, conversation_repo=None, message_repo=None,
                 query_log_repo=None, assistant_log_repo=None) -> None: ...

    async def ask(
        self,
        question: str,
        *,
        conversation_id: str | None = None,
        filters: RetrievalFilters | None = None,
    ) -> ChatMessage
```

Answers `question`, persisting the full turn regardless of whether SQL generation or retrieval
succeeded: creates a new `Conversation` if `conversation_id` is `None`; loads the last 20 messages
(uses the last 6 turns as prompt context); stores the user's message; attempts
`SQLGenerator.generate_and_execute` (falls back silently to retrieval-only on any failure, e.g.
`UnsafeSQLError` or no `SUPABASE_DB_URL` configured); always runs
`RetrievalService.hybrid_search(question, filters, limit=8)`; builds a combined context string from
SQL rows (first 20) and retrieved records (each truncated to 500 chars); calls the OpenAI chat
completion (`temperature=0.3`) with `ASSISTANT_SYSTEM_PROMPT` plus, if there is history, the
`CONVERSATION_MEMORY_PROMPT`; persists the assistant's `ChatMessage` (with `sources`,
`sql_generated`, `model_used`, `execution_time_ms`, token counts), a `QueryLog`, and an
`AssistantLog`; returns the assistant `ChatMessage`.

```python
from app.ai.assistant import Assistant
from app.retrieval import RetrievalFilters

assistant = Assistant()
reply = await assistant.ask(
    "Compare Instagram and X engagement for posts about climate change",
    filters=RetrievalFilters(hashtag="climate"),
)
print(reply.content)
```

## `RetrievalService` (`app/retrieval/service.py`)

The only layer the AI assistant (or any other caller) should use to fetch "relevant records" —
never query `documents`/`embeddings`/`posts` directly.

```python
class RetrievalService:
    def __init__(self, embedding_provider=None, embedding_repo=None, post_repo=None,
                 author_repo=None, engagement_repo=None) -> None: ...

    async def keyword_search(self, query: str, *, platform: str | None = None, limit: int = 20) -> list[RetrievalResult]
    async def semantic_search(self, query: str, *, platform: str | None = None, limit: int = 20) -> list[RetrievalResult]
    async def hybrid_search(self, query: str, filters: RetrievalFilters | None = None, *, limit: int = 10) -> list[RetrievalResult]
    async def popular_posts(self, *, platform: str | None = None, limit: int = 10) -> list[RetrievalResult]
```

- `keyword_search` — full-text search over `documents.search_vector` (a generated Postgres
  `tsvector`); platform filtering and the result cap are applied in Python after the query runs
  (PostgREST's `text_search()` builder doesn't support further chaining).
- `semantic_search` — embeds `query` via the configured `EmbeddingProvider`, then calls the
  `match_embeddings` RPC (cosine similarity over `pgvector`, computed in the database).
- `hybrid_search` — runs both concurrently (fetching `limit * 2` candidates each), merges by
  `(source_type, source_id)`, combines scores as `keyword * 0.4 + semantic * 0.6`, applies
  `RetrievalFilters` (author, hashtag, date range, min likes, content type — each requires a
  `posts` lookup per candidate), sorts descending, and returns the top `limit`.
- `popular_posts` — bypasses text/vector search entirely; ranks by `engagement.likes` directly,
  for questions like "most liked posts this month" where there is no keyword/semantic query.

All four return `list[RetrievalResult]` (`source_type`, `source_id`, `platform`, `content`,
`score`, `metadata`).

```python
from app.retrieval import RetrievalService, RetrievalFilters

retrieval = RetrievalService()
results = await retrieval.hybrid_search(
    "sentiment about OpenAI",
    RetrievalFilters(platform="twitter", min_likes=100),
    limit=5,
)
for r in results:
    print(r.platform, r.source_type, r.score, r.content[:80])
```

## Other notable internal APIs

- **`SQLGenerator`** (`app/ai/sql_generator.py`): `generate(question, conversation_context="") ->
  str` (raw SQL text) and `generate_and_execute(question, conversation_context="") -> tuple[str,
  list[dict]]` (validated + executed, raising `UnsafeSQLError` if the generated statement fails
  `app.database.assert_sql_is_safe`).
- **`EmbeddingService`** (`app/embeddings/service.py`): `embed_batch(items: list[EmbeddableItem]) ->
  int` (checksum-aware batch embed + persist, returns count actually re-embedded) and
  `embed_one(item) -> int`.
- **`BaseRepository[ModelT]`** (`app/repositories/base.py`): generic CRUD (`get_by_id`,
  `require_by_id`, `list_all`, `create`, `upsert`, `bulk_upsert`, `update`, `soft_delete`, `count`)
  inherited by every concrete repository (`AuthorRepository`, `PostRepository`, etc.) — see
  `docs/developer_guide.md` for how to add a new one.
