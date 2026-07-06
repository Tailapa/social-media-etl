"""Integration tests for app/ingestion/pipeline.py's IngestionPipeline.

Exercises the full ingest() flow (dedupe -> bulk upsert -> FK remap ->
two-phase comment-parent linking -> hashtags/mentions/engagement ->
embeddings) against in-memory fake repositories that mimic the
natural-key-upsert semantics of the real Supabase-backed repositories (see
`app/repositories/base.py::BaseRepository._serialize_for_upsert`): a fake
repo stores rows keyed by natural key, assigns a fresh id only the first
time a key is seen, and preserves that id on every subsequent upsert of the
same key — exactly what the real Postgres upsert-on-conflict does. No real
Supabase/network/API calls happen anywhere in this file.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from typing import Any

from app.apify.base.scraper import ScrapeResult
from app.ingestion.pipeline import IngestionPipeline
from app.normalization.instagram import normalize_author, normalize_comment, normalize_post

# --- Fake repositories -------------------------------------------------------


def _bulk_upsert(store: dict[str, Any], items: list[Any]) -> list[Any]:
    """Shared upsert-by-natural-key semantics for every fake entity repo:
    first upsert of a `dedup_key` assigns/keeps the client-generated id,
    every subsequent upsert of the same key preserves the *stored* id —
    mirroring `_serialize_for_upsert` dropping `id` from the payload so
    Postgres never overwrites an existing primary key.
    """
    persisted = []
    for item in items:
        key = item.dedup_key
        existing = store.get(key)
        if existing is not None:
            item = item.model_copy(update={"id": existing.id})
        store[key] = item
        persisted.append(item)
    return persisted


class FakeAuthorRepo:
    def __init__(self) -> None:
        self.store: dict[str, Any] = {}
        self.bulk_calls: list[list[Any]] = []

    async def bulk_upsert_authors(self, authors: list[Any]) -> list[Any]:
        self.bulk_calls.append(list(authors))
        return _bulk_upsert(self.store, authors)


class FakePostRepo:
    def __init__(self) -> None:
        self.store: dict[str, Any] = {}
        self.bulk_calls: list[list[Any]] = []

    async def bulk_upsert_posts(self, posts: list[Any]) -> list[Any]:
        self.bulk_calls.append(list(posts))
        return _bulk_upsert(self.store, posts)


class FakeCommentRepo:
    def __init__(self, *, raise_on_bulk_upsert: bool = False) -> None:
        self.store: dict[str, Any] = {}
        self.bulk_calls: list[list[Any]] = []
        self.updates: list[tuple[str, dict]] = []
        self._raise_on_bulk_upsert = raise_on_bulk_upsert

    async def bulk_upsert_comments(self, comments: list[Any]) -> list[Any]:
        self.bulk_calls.append(list(comments))
        if self._raise_on_bulk_upsert:
            raise RuntimeError("simulated comment upsert failure")
        return _bulk_upsert(self.store, comments)

    async def update(self, record_id: str, data: dict) -> Any:
        for key, comment in self.store.items():
            if str(comment.id) == str(record_id):
                updated = comment.model_copy(update=data)
                self.store[key] = updated
                self.updates.append((record_id, data))
                return updated
        raise LookupError(f"no fake comment with id {record_id}")


class FakeChannelRepo:
    def __init__(self) -> None:
        self.store: dict[str, Any] = {}

    async def bulk_upsert_channels(self, channels: list[Any]) -> list[Any]:
        return _bulk_upsert(self.store, channels)


class FakeVideoRepo:
    def __init__(self) -> None:
        self.store: dict[str, Any] = {}

    async def bulk_upsert_videos(self, videos: list[Any]) -> list[Any]:
        return _bulk_upsert(self.store, videos)


class FakeMediaRepo:
    def __init__(self) -> None:
        self.by_post_store: dict[str, list[Any]] = {}

    async def by_post(self, post_id: str) -> list[Any]:
        return list(self.by_post_store.get(post_id, []))

    async def bulk_create_media(self, media_items: list[Any]) -> list[Any]:
        for media in media_items:
            self.by_post_store.setdefault(media.post_id, []).append(media)
        return media_items


class FakeHashtagRepo:
    def __init__(self) -> None:
        self.store: dict[str, Any] = {}

    async def bulk_upsert_tags(self, hashtags: list[Any]) -> list[Any]:
        persisted = []
        for tag in hashtags:
            existing = self.store.get(tag.tag)
            if existing is not None:
                tag = tag.model_copy(update={"id": existing.id})
            self.store[tag.tag] = tag
            persisted.append(tag)
        return persisted


class FakePostHashtagRepo:
    def __init__(self) -> None:
        self.links: list[Any] = []

    async def bulk_link(self, links: list[Any]) -> None:
        self.links.extend(links)


class FakeMentionRepo:
    def __init__(self) -> None:
        self.by_post_store: dict[str, list[Any]] = {}

    async def by_post(self, post_id: str) -> list[Any]:
        return list(self.by_post_store.get(post_id, []))

    async def bulk_create_mentions(self, mentions: list[Any]) -> list[Any]:
        for mention in mentions:
            self.by_post_store.setdefault(mention.post_id, []).append(mention)
        return mentions


class FakeEngagementRepo:
    def __init__(self) -> None:
        self.store: dict[str, Any] = {}
        self.calls: list[Any] = []

    async def upsert_for_post(self, engagement: Any) -> Any:
        self.calls.append(engagement)
        self.store[engagement.post_id] = engagement
        return engagement


class FakeScrapeJobRepo:
    def __init__(self) -> None:
        self.started: list[Any] = []
        self.succeeded: list[tuple[str, int]] = []
        self.partial: list[tuple[str, int, str]] = []
        self.failed: list[tuple[str, str]] = []

    async def start(self, platform: str, job_type: str, target: str | None = None) -> Any:
        job = SimpleNamespace(id=uuid.uuid4(), platform=platform, job_type=job_type, target=target)
        self.started.append(job)
        return job

    async def mark_succeeded(self, job_id: str, records_scraped: int) -> None:
        self.succeeded.append((job_id, records_scraped))

    async def mark_partial(self, job_id: str, records_scraped: int, error: str) -> None:
        self.partial.append((job_id, records_scraped, error))

    async def mark_failed(self, job_id: str, error: str) -> None:
        self.failed.append((job_id, error))


class FakeEmbeddingService:
    def __init__(self) -> None:
        self.batches: list[list[Any]] = []

    async def embed_batch(self, items: list[Any]) -> int:
        self.batches.append(list(items))
        return len(items)


# --- Fixtures ------------------------------------------------------------


class Repos:
    """Bag of every fake repo/service so tests can inspect internal state
    after calling `pipeline.ingest(...)`.
    """

    def __init__(self, *, comment_raises: bool = False) -> None:
        self.author_repo = FakeAuthorRepo()
        self.channel_repo = FakeChannelRepo()
        self.video_repo = FakeVideoRepo()
        self.post_repo = FakePostRepo()
        self.comment_repo = FakeCommentRepo(raise_on_bulk_upsert=comment_raises)
        self.media_repo = FakeMediaRepo()
        self.hashtag_repo = FakeHashtagRepo()
        self.post_hashtag_repo = FakePostHashtagRepo()
        self.mention_repo = FakeMentionRepo()
        self.engagement_repo = FakeEngagementRepo()
        self.scrape_job_repo = FakeScrapeJobRepo()
        self.embedding_service = FakeEmbeddingService()

    def build_pipeline(self) -> IngestionPipeline:
        return IngestionPipeline(
            author_repo=self.author_repo,
            channel_repo=self.channel_repo,
            video_repo=self.video_repo,
            post_repo=self.post_repo,
            comment_repo=self.comment_repo,
            media_repo=self.media_repo,
            hashtag_repo=self.hashtag_repo,
            post_hashtag_repo=self.post_hashtag_repo,
            mention_repo=self.mention_repo,
            engagement_repo=self.engagement_repo,
            scrape_job_repo=self.scrape_job_repo,
            embedding_service=self.embedding_service,
        )


def _build_scrape_result() -> ScrapeResult:
    """Author -> post -> root comment -> reply, with a hashtag and mention
    embedded in the post caption, all produced through the real Instagram
    normalizers (not hand-built Pydantic models) so this exercises the same
    shape the scrapers actually produce.
    """
    author = normalize_author({"id": "ig-owner-1", "username": "alice", "followersCount": 500})
    post = normalize_post(
        {
            "id": "ig-post-1",
            "shortCode": "abc123",
            "caption": "Hello #world @bob check this out",
            "timestamp": "2024-01-01T00:00:00Z",
            "likesCount": 10,
            "commentsCount": 2,
            "displayUrl": "https://example.com/photo.jpg",
        },
        author_id=str(author.id),
    )
    root_comment = normalize_comment(
        {"id": "ig-comment-1", "text": "Nice post!"},
        post_id=str(post.id),
        author_id=str(author.id),
    )
    reply_comment = normalize_comment(
        {"id": "ig-comment-2", "text": "Totally agree!"},
        post_id=str(post.id),
        author_id=str(author.id),
        parent_id=str(root_comment.id),
    )
    return ScrapeResult(
        authors=[author],
        posts=[post],
        comments=[root_comment, reply_comment],
        raw_item_count=4,
    )


# --- Tests -----------------------------------------------------------------


async def test_ingest_happy_path_counts_and_relinking():
    repos = Repos()
    pipeline = repos.build_pipeline()
    result = _build_scrape_result()

    report = await pipeline.ingest(result, platform="instagram", job_type="posts", target="test")

    assert report.errors == []
    assert report.authors_upserted == 1
    assert report.posts_upserted == 1
    assert report.comments_upserted == 2
    assert report.hashtags_linked == 1
    assert report.mentions_created == 1
    assert report.engagement_upserted == 1
    assert report.embeddings_generated == 3  # post caption + 2 comments

    # The reply's persisted parent_comment_id must point at the *persisted*
    # id of the root comment, not the local client-generated id.
    persisted_root = repos.comment_repo.store[result.comments[0].dedup_key]
    persisted_reply = repos.comment_repo.store[result.comments[1].dedup_key]
    assert persisted_reply.parent_comment_id == str(persisted_root.id)

    # Scrape job was marked succeeded, not partial/failed.
    assert len(repos.scrape_job_repo.succeeded) == 1
    assert repos.scrape_job_repo.partial == []
    assert repos.scrape_job_repo.failed == []


async def test_ingest_is_idempotent_on_rerun():
    repos = Repos()
    pipeline = repos.build_pipeline()
    result = _build_scrape_result()

    report1 = await pipeline.ingest(result, platform="instagram", job_type="posts", target="test")
    author_id_run1 = repos.author_repo.store[result.authors[0].dedup_key].id
    post_id_run1 = repos.post_repo.store[result.posts[0].dedup_key].id

    report2 = await pipeline.ingest(result, platform="instagram", job_type="posts", target="test")
    author_id_run2 = repos.author_repo.store[result.authors[0].dedup_key].id
    post_id_run2 = repos.post_repo.store[result.posts[0].dedup_key].id

    assert report1.errors == []
    assert report2.errors == []
    assert author_id_run1 == author_id_run2
    assert post_id_run1 == post_id_run2

    # Only one author/post/pair-of-comments ever accumulates in the store —
    # re-ingestion merges onto the same natural-key rows, it doesn't duplicate.
    assert len(repos.author_repo.store) == 1
    assert len(repos.post_repo.store) == 1
    assert len(repos.comment_repo.store) == 2

    # Both runs succeeded (no partial/failed job).
    assert len(repos.scrape_job_repo.succeeded) == 2
    assert repos.scrape_job_repo.partial == []


async def test_ingest_isolates_a_failing_entity_and_marks_job_partial():
    repos = Repos(comment_raises=True)
    pipeline = repos.build_pipeline()
    result = _build_scrape_result()

    report = await pipeline.ingest(result, platform="instagram", job_type="posts", target="test")

    # The pipeline must not raise/crash even though comment upsert blew up.
    assert any("comment" in err for err in report.errors)
    assert report.comments_upserted == 0

    # Everything independent of comments still went through.
    assert report.authors_upserted == 1
    assert report.posts_upserted == 1
    assert report.hashtags_linked == 1
    assert report.mentions_created == 1
    assert report.engagement_upserted == 1

    # Job is marked partial (recoverable), never failed (fatal).
    assert len(repos.scrape_job_repo.partial) == 1
    assert repos.scrape_job_repo.failed == []
    assert repos.scrape_job_repo.succeeded == []


async def test_ingest_marks_job_failed_on_fatal_error():
    """Errors inside every individual ingestion step are isolated (see the
    "partial" test above) — but something raising *outside* all of those
    isolation boundaries (e.g. `dedupe_by_key` choking on a malformed item
    before any repo call happens) is fatal for the whole run: `ingest()`'s
    outer try/except must catch it, mark the job failed (not partial), and
    still return a report rather than propagating the exception.
    """
    repos = Repos()
    pipeline = repos.build_pipeline()

    # An author-shaped object with no `dedup_key` attribute blows up the very
    # first line of `_run` (`dedupe_by_key(result.authors, lambda a: a.dedup_key)`)
    # before any repo/report bookkeeping has happened for this run.
    broken_result = ScrapeResult(authors=[SimpleNamespace(username="broken")])

    report = await pipeline.ingest(
        broken_result, platform="instagram", job_type="posts", target="test"
    )

    assert any("fatal" in err for err in report.errors)
    assert len(repos.scrape_job_repo.failed) == 1
    assert repos.scrape_job_repo.partial == []
    assert repos.scrape_job_repo.succeeded == []
