# Example Workflows

Five realistic end-to-end walkthroughs using the actual CLI scripts and internal APIs described in
`docs/api_documentation.md`. All examples assume you've completed `docs/setup_guide.md` (env
configured, migrations applied).

## Workflow A: scrape an Instagram profile and ask the assistant about it

1. Scrape the last 30 posts from a profile:

   ```bash
   python scripts/run_scrape.py instagram posts nasa --limit 30
   ```

   This runs `ScrapeService.scrape_posts("instagram", "nasa", limit=30)` ->
   `InstagramScraper.scrape_posts` (via `apify_instagram_post_actor`) -> normalization -> the full
   `IngestionPipeline.ingest` chain (authors -> posts -> media -> hashtags -> mentions -> engagement
   -> embeddings). It prints an `IngestionReport` summary, e.g.:

   ```
   Job 3fae21e0-...: 31 records processed
     authors=1 channels=0 posts=30 videos=0 comments=0
     media=42 hashtags_linked=87 mentions_created=5 engagement=30
     embeddings_generated=30
   ```

2. Also pull comments for a specific post (needed since `scrape_posts` doesn't fetch comments):

   ```bash
   python scripts/run_scrape.py instagram comments https://www.instagram.com/p/SOME_SHORTCODE/ --limit 100
   ```

3. Launch the chat UI and ask about the data:

   ```bash
   python scripts/launch_gradio.py
   ```

   In the Chat tab, ask: *"What were NASA's most liked Instagram posts?"* — `Assistant.ask` will
   attempt SQL generation (a query joining `posts` -> `engagement` filtered on `platform =
   'instagram'` and the author's username), fall back to/augment with hybrid retrieval over the
   ingested captions, and answer with citations like `(instagram, nasa, a1b2c3d4)`.

   Equivalently, from Python directly:

   ```python
   import asyncio
   from app.services.chat_service import ChatService

   async def main():
       chat = ChatService()
       reply = await chat.ask("What were NASA's most liked Instagram posts?")
       print(reply.content)
       print("Sources:", reply.sources)

   asyncio.run(main())
   ```

## Workflow B: scrape a Twitter hashtag and compare engagement across platforms

1. Scrape recent tweets for a hashtag:

   ```bash
   python scripts/run_scrape.py twitter hashtag climate --limit 100
   ```

   `TwitterScraper.scrape_hashtag` runs a single `apidojo/tweet-scraper` search with
   `searchTerms=["#climate"]` — every method on `TwitterScraper` funnels through this one
   search-driven actor, differing only in the search term (see `app/apify/twitter/scraper.py`).

2. Scrape the same topic on Instagram for comparison:

   ```bash
   python scripts/run_scrape.py instagram hashtag climate --limit 100
   ```

3. Ask the assistant to compare them — this exercises `Assistant.ask`'s SQL-generation path most
   directly, since "compare platforms" implies an aggregate query the retrieval layer alone can't
   answer well:

   ```python
   import asyncio
   from app.services.chat_service import ChatService

   async def main():
       chat = ChatService()
       reply = await chat.ask("Compare Instagram and X engagement for posts about climate change")
       print(reply.content)
       print("Generated SQL:", reply.sql_generated)

   asyncio.run(main())
   ```

   `SQLGenerator.generate_and_execute` produces something like a `GROUP BY platform` query joining
   `posts` and `engagement`, validated by `assert_sql_is_safe` (read-only, known tables only)
   before it runs against `SUPABASE_DB_URL`. If SQL generation is unavailable or fails validation,
   `Assistant.ask` silently falls back to `RetrievalService.hybrid_search` results only — the
   answer still comes back, just grounded in retrieved snippets rather than an aggregate query.

4. Or query the analytics dashboard directly for the same comparison without going through the AI
   assistant at all:

   ```python
   import asyncio
   from app.services.analytics_service import AnalyticsService

   async def main():
       analytics = AnalyticsService()
       print(await analytics.platform_distribution())
       print(await analytics.top_engagement_posts(limit=5))

   asyncio.run(main())
   ```

## Workflow C: backfill embeddings for existing content

**There is no dedicated `scripts/backfill_embeddings.py` in this repository today** — a docstring
in `app/embeddings/service.py` references one ("available standalone for backfills") but the
script itself has not been written yet. Until it exists, backfilling is a short ad hoc script
using `EmbeddingService` and the post/comment repositories directly:

```python
import asyncio

from app.embeddings.service import EmbeddableItem, EmbeddingService
from app.models.pydantic.enums import EmbeddingSourceType
from app.repositories.post_repository import PostRepository


async def backfill_post_embeddings(platform: str | None = None, limit: int = 500) -> None:
    post_repo = PostRepository()
    embedding_service = EmbeddingService()

    posts = (
        await post_repo.by_platform(platform, limit=limit)
        if platform
        else await post_repo.list_all(limit=limit)
    )

    items = [
        EmbeddableItem(
            source_type=EmbeddingSourceType.POST,
            source_id=str(post.id),
            platform=post.platform,
            text=post.caption or post.content or "",
        )
        for post in posts
        if (post.caption or post.content or "").strip()
    ]

    count = await embedding_service.embed_batch(items)
    print(f"Re-embedded {count} of {len(items)} candidate posts (rest were unchanged, skipped by checksum)")


asyncio.run(backfill_post_embeddings(platform="instagram"))
```

Because `EmbeddingService.embed_batch` compares each item's SHA-256 checksum against the existing
`embeddings` row for that `(source_id, source_type, model)` before calling OpenAI (see
`docs/architecture.md`'s "Key design decisions"), running this repeatedly is cheap — only posts
whose caption/content actually changed since the last embedding run incur an API call. The same
pattern applies to comments (`EmbeddingSourceType.COMMENT`, text from `Comment.content`) and video
transcripts (`EmbeddingSourceType.TRANSCRIPT`, text from `Video.transcript`, only for videos where
`has_transcript` is true).

If you build this out into a real script, follow the existing CLI conventions in `scripts/` (a
`sys.path.insert` bootstrap, `argparse`, an `async def run(...)` entrypoint called from
`asyncio.run` in `main()` — see `scripts/run_scrape.py` for the pattern).

## Workflow D: using the Gradio UI's chat and analytics tabs

```bash
python scripts/launch_gradio.py
```

**Chat tab** (`app/gradio/chat_tab.py`):

- Type a question and press Enter or click **Send** — your message is echoed immediately, then a
  `_Thinking..._` placeholder appears while `ChatService.ask` runs, then the real answer replaces
  it (this is not token-by-token streaming — `Assistant.ask` awaits one full OpenAI completion).
- **New chat** clears the visible conversation locally; the actual `conversations` row isn't
  created until you send the first message (`Assistant.ask` lazily creates it when
  `conversation_id` is `None`).
- **Clear chat** soft-deletes the current conversation server-side (`ConversationRepository.
  soft_delete`) and resets the UI to blank.
- The left **sidebar** lists recent conversations (`ChatService.list_conversations`, ordered by
  `updated_at` descending) and supports **Search** by title
  (`ChatService.search_conversations`, `ILIKE %query%` against `conversations.title`).
- **Export as Markdown** downloads the selected conversation rendered via
  `ChatService.export_conversation` (speaker labels, timestamps, and cited sources per message).

**Analytics tab** (`app/gradio/analytics_tab.py`):

- Click **Refresh** to populate every tile from a single `AnalyticsService.dashboard_summary()`
  call: total posts/comments, a platform-distribution bar chart, trending hashtags, most active
  authors, top-engagement posts, recent scrape jobs, and AI query stats (total queries + average
  latency across the last 200 `query_logs` rows).
- If Supabase credentials aren't configured (or any backend call fails), the tab shows a friendly
  inline banner ("Could not load analytics: ...") instead of crashing the UI — this is intentional
  (`app/gradio/analytics_tab.py`'s `_refresh` wraps `dashboard_summary()` in a `try/except`) so the
  Blocks app remains usable for layout/demo purposes even without a live backend.

## Workflow E: scrape a YouTube channel, pull transcripts, and ask about video content

YouTube is the one platform where a single raw item produces two rows for the same real-world
video: a `Post` (so it slots into the unified content/retrieval model every other platform uses)
and a `Video` (duration/transcript fields — see `app/models/pydantic/channel.py`). It's also the
only scraper with no `scrape_hashtag`/`scrape_keyword` support — `YouTubeScraper` intentionally
leaves those unoverridden, so calling them raises `NotImplementedError` (see
`app/apify/youtube/scraper.py`).

1. Scrape the channel's own metadata first (`YouTubeScraper.scrape_profile`, backed by
   `apify_youtube_scraper_actor` pointed at the channel's landing page):

   ```bash
   python scripts/run_scrape.py youtube profile @NASA
   ```

   This produces one `Author` (the channel owner, normalized like every other platform's profile)
   and one `Channel` (subscriber/video-count semantics). The identifier is whatever comes after
   `youtube.com/` for the channel — a handle like `@NASA`, or a legacy `/channel/UC...` /
   `/c/...` path.

2. Scrape its recent videos (`YouTubeScraper.scrape_posts`, same actor pointed at the channel's
   `/videos` tab instead):

   ```bash
   python scripts/run_scrape.py youtube posts @NASA --limit 8
   ```

   Each raw item becomes an `Author` + `Channel` pair (deduped across videos from the same
   channel via the `get_or_register` cache in `app/normalization/common.py` — every video repeats
   the same embedded channel info, and reusing one canonical object per channel/author is what
   keeps `Video.channel_id`/`Post.author_id` pointing at a row that actually gets persisted) plus
   a `Post` + `Video` pair for the video itself. Because `--limit 8` is at or under
   `_TRANSCRIPT_FETCH_LIMIT` (10, see `app/apify/youtube/scraper.py`), the scraper also attempts a
   best-effort transcript fetch per video via `apify_youtube_transcript_actor` — a transcript
   fetch failure is caught and logged (`"Transcript fetch failed, continuing without it"`), never
   fatal to the batch. Requesting more than 10 videos skips transcript fetching entirely (fetching
   one is a full extra actor run per video, too slow for a large pull):

   ```bash
   python scripts/run_scrape.py youtube posts @NASA --limit 50   # no transcripts fetched
   ```

3. Pull comments for one specific video (`YouTubeScraper.scrape_comments` accepts either a full
   `https://www.youtube.com/watch?v=...` URL or a bare video id):

   ```bash
   python scripts/run_scrape.py youtube comments dQw4w9WgXcQ --limit 100
   ```

4. Ask the assistant a question that can only be answered from transcript text, not the caption —
   this exercises `RetrievalService`'s embedding of `EmbeddingSourceType.TRANSCRIPT` documents
   (only for videos where `Video.has_transcript` is true) alongside the usual post/comment
   embeddings:

   ```python
   import asyncio
   from app.services.chat_service import ChatService

   async def main():
       chat = ChatService()
       reply = await chat.ask("What did NASA's most recent video actually say about the mission?")
       print(reply.content)
       print("Sources:", reply.sources)

   asyncio.run(main())
   ```

   If the video's transcript wasn't available (best-effort, not guaranteed — some videos have
   captions disabled), `Assistant.ask` still answers from the caption/description text alone
   rather than failing; it only cites transcript content when that document actually exists.
