"""The ingestion pipeline: Apify -> Raw JSON -> Validation -> Pydantic ->
Normalization -> Deduplication -> Supabase -> Embedding generation -> Vector
storage.

Scrapers (`app/apify/*`) already return normalized Pydantic models wrapped
in a `ScrapeResult` (validation + normalization happened there). This module
owns everything downstream: batch deduplication, persisting through the
repository layer with correct FK remapping (client-generated IDs are never
trusted once a row round-trips through an upsert — see
`BaseRepository._serialize_for_upsert`), building `engagement`/`hashtags`/
`mentions` rows, and triggering the embedding pipeline. Every step isolates
failures (log + skip) rather than aborting the whole run, and `ScrapeJob` is
updated throughout so a run's progress can always be inspected.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, TypeVar

from pydantic import BaseModel

from app.apify.base.scraper import ScrapeResult
from app.embeddings.service import EmbeddableItem, EmbeddingService
from app.logging import get_logger
from app.models.pydantic import Hashtag, Mention, Post, PostHashtag
from app.models.pydantic.enums import EmbeddingSourceType
from app.normalization import NORMALIZERS, dedupe_by_key
from app.repositories.author_repository import AuthorRepository
from app.repositories.channel_repository import ChannelRepository, VideoRepository
from app.repositories.comment_repository import CommentRepository
from app.repositories.engagement_repository import EngagementRepository
from app.repositories.hashtag_repository import HashtagRepository, PostHashtagRepository
from app.repositories.media_repository import MediaRepository
from app.repositories.mention_repository import MentionRepository
from app.repositories.post_repository import PostRepository
from app.repositories.scrape_job_repository import ScrapeJobRepository

logger = get_logger(__name__)

ModelT = TypeVar("ModelT", bound=BaseModel)


@dataclass(slots=True)
class IngestionReport:
    """Summary of one pipeline run — returned to callers (CLI scripts, the
    Gradio "run a scrape" action) for progress reporting.
    """

    job_id: str | None = None
    authors_upserted: int = 0
    channels_upserted: int = 0
    posts_upserted: int = 0
    videos_upserted: int = 0
    comments_upserted: int = 0
    media_created: int = 0
    hashtags_linked: int = 0
    mentions_created: int = 0
    engagement_upserted: int = 0
    embeddings_generated: int = 0
    errors: list[str] = field(default_factory=list)

    @property
    def total_records(self) -> int:
        return (
            self.authors_upserted
            + self.channels_upserted
            + self.posts_upserted
            + self.videos_upserted
            + self.comments_upserted
        )


def _build_id_map(local_items: list[Any], persisted_items: list[Any]) -> dict[str, str]:
    """Map each local (client-generated) `.id` to the DB-persisted `.id`,
    matched by the model's stable `dedup_key` rather than list position —
    Postgres does not guarantee a bulk upsert's response order matches the
    input order.
    """
    persisted_by_key = {item.dedup_key: str(item.id) for item in persisted_items}
    return {
        str(item.id): persisted_by_key[item.dedup_key]
        for item in local_items
        if item.dedup_key in persisted_by_key
    }


class IngestionPipeline:
    def __init__(
        self,
        *,
        author_repo: AuthorRepository | None = None,
        channel_repo: ChannelRepository | None = None,
        video_repo: VideoRepository | None = None,
        post_repo: PostRepository | None = None,
        comment_repo: CommentRepository | None = None,
        media_repo: MediaRepository | None = None,
        hashtag_repo: HashtagRepository | None = None,
        post_hashtag_repo: PostHashtagRepository | None = None,
        mention_repo: MentionRepository | None = None,
        engagement_repo: EngagementRepository | None = None,
        scrape_job_repo: ScrapeJobRepository | None = None,
        embedding_service: EmbeddingService | None = None,
    ) -> None:
        self.author_repo = author_repo or AuthorRepository()
        self.channel_repo = channel_repo or ChannelRepository()
        self.video_repo = video_repo or VideoRepository()
        self.post_repo = post_repo or PostRepository()
        self.comment_repo = comment_repo or CommentRepository()
        self.media_repo = media_repo or MediaRepository()
        self.hashtag_repo = hashtag_repo or HashtagRepository()
        self.post_hashtag_repo = post_hashtag_repo or PostHashtagRepository()
        self.mention_repo = mention_repo or MentionRepository()
        self.engagement_repo = engagement_repo or EngagementRepository()
        self.scrape_job_repo = scrape_job_repo or ScrapeJobRepository()
        self.embedding_service = embedding_service or EmbeddingService()

    async def ingest(
        self,
        result: ScrapeResult,
        *,
        platform: str,
        job_type: str,
        target: str | None = None,
    ) -> IngestionReport:
        job = await self.scrape_job_repo.start(platform, job_type, target)
        report = IngestionReport(job_id=str(job.id))
        try:
            await self._run(result, report)
            if report.errors:
                await self.scrape_job_repo.mark_partial(
                    str(job.id), report.total_records, "; ".join(report.errors[:20])
                )
            else:
                await self.scrape_job_repo.mark_succeeded(str(job.id), report.total_records)
        except Exception as exc:  # pipeline-fatal: nothing further could be salvaged
            logger.exception("Ingestion pipeline failed", job_id=str(job.id))
            await self.scrape_job_repo.mark_failed(str(job.id), str(exc))
            report.errors.append(f"fatal: {exc}")
        return report

    async def _run(self, result: ScrapeResult, report: IngestionReport) -> None:
        authors = dedupe_by_key(result.authors, lambda a: a.dedup_key)
        persisted_authors = await self._safe_bulk(
            self.author_repo.bulk_upsert_authors, authors, report, "author"
        )
        author_id_map = _build_id_map(authors, persisted_authors)
        report.authors_upserted = len(persisted_authors)

        channels = dedupe_by_key(result.channels, lambda c: c.dedup_key)
        remapped_channels = [
            c.model_copy(update={"author_id": author_id_map.get(c.author_id, c.author_id)})
            for c in channels
        ]
        persisted_channels = await self._safe_bulk(
            self.channel_repo.bulk_upsert_channels, remapped_channels, report, "channel"
        )
        channel_id_map = _build_id_map(remapped_channels, persisted_channels)
        report.channels_upserted = len(persisted_channels)

        posts = dedupe_by_key(result.posts, lambda p: p.dedup_key)
        remapped_posts = [
            p.model_copy(update={"author_id": author_id_map.get(p.author_id, p.author_id)})
            for p in posts
        ]
        persisted_posts = await self._safe_bulk(
            self.post_repo.bulk_upsert_posts, remapped_posts, report, "post"
        )
        post_id_map = _build_id_map(remapped_posts, persisted_posts)
        report.posts_upserted = len(persisted_posts)

        videos = dedupe_by_key(result.videos, lambda v: v.dedup_key)
        remapped_videos = [
            v.model_copy(
                update={
                    "channel_id": channel_id_map.get(v.channel_id, v.channel_id),
                    "post_id": post_id_map.get(v.post_id, v.post_id) if v.post_id else None,
                }
            )
            for v in videos
        ]
        persisted_videos = await self._safe_bulk(
            self.video_repo.bulk_upsert_videos, remapped_videos, report, "video"
        )
        report.videos_upserted = len(persisted_videos)

        comments = dedupe_by_key(result.comments, lambda c: c.dedup_key)
        remapped_comments = [
            c.model_copy(
                update={
                    "post_id": post_id_map.get(c.post_id, c.post_id),
                    "author_id": author_id_map.get(c.author_id, c.author_id),
                    "parent_comment_id": None,  # linked in a second pass below
                }
            )
            for c in comments
        ]
        persisted_comments = await self._safe_bulk(
            self.comment_repo.bulk_upsert_comments, remapped_comments, report, "comment"
        )
        comment_id_map = _build_id_map(remapped_comments, persisted_comments)
        report.comments_upserted = len(persisted_comments)
        await self._relink_comment_parents(comments, comment_id_map, report)

        await self._ingest_media(remapped_posts, post_id_map, report)
        await self._ingest_hashtags(remapped_posts, post_id_map, report)
        await self._ingest_mentions(remapped_posts, post_id_map, report)
        await self._ingest_engagement(remapped_posts, post_id_map, report)
        await self._generate_embeddings(
            persisted_posts, persisted_comments, persisted_videos, report
        )

    async def _relink_comment_parents(
        self, original_comments: list[Any], comment_id_map: dict[str, str], report: IngestionReport
    ) -> None:
        """Second pass: point replies at their parent's *persisted* id, now
        that every comment in the batch has one.
        """
        for comment in original_comments:
            if not comment.parent_comment_id:
                continue
            new_id = comment_id_map.get(str(comment.id))
            new_parent_id = comment_id_map.get(comment.parent_comment_id)
            if new_id and new_parent_id:
                try:
                    await self.comment_repo.update(new_id, {"parent_comment_id": new_parent_id})
                except Exception as exc:  # noqa: BLE001 - isolate, don't abort the run
                    report.errors.append(f"comment parent link failed: {exc}")

    async def _ingest_media(
        self, posts: list[Post], post_id_map: dict[str, str], report: IngestionReport
    ) -> None:
        for post in posts:
            persisted_post_id = post_id_map.get(str(post.id))
            if not persisted_post_id or not post.media:
                continue
            try:
                existing = await self.media_repo.by_post(persisted_post_id)
                existing_urls = {m.url for m in existing}
                new_media = [
                    m.model_copy(update={"post_id": persisted_post_id})
                    for m in post.media
                    if m.url not in existing_urls
                ]
                created = await self.media_repo.bulk_create_media(new_media)
                report.media_created += len(created)
            except Exception as exc:  # noqa: BLE001
                report.errors.append(f"media ingestion failed for post {persisted_post_id}: {exc}")

    async def _ingest_hashtags(
        self, posts: list[Post], post_id_map: dict[str, str], report: IngestionReport
    ) -> None:
        all_tags = {tag for post in posts for tag in post.hashtags}
        if not all_tags:
            return
        try:
            hashtags = [Hashtag(tag=tag) for tag in all_tags]
            persisted_tags = await self.hashtag_repo.bulk_upsert_tags(hashtags)
            tag_id_map = {h.tag: str(h.id) for h in persisted_tags}
        except Exception as exc:  # noqa: BLE001
            report.errors.append(f"hashtag upsert failed: {exc}")
            return

        links: list[PostHashtag] = []
        for post in posts:
            persisted_post_id = post_id_map.get(str(post.id))
            if not persisted_post_id:
                continue
            for tag in post.hashtags:
                hashtag_id = tag_id_map.get(tag)
                if hashtag_id:
                    links.append(PostHashtag(post_id=persisted_post_id, hashtag_id=hashtag_id))
        try:
            await self.post_hashtag_repo.bulk_link(links)
            report.hashtags_linked = len(links)
        except Exception as exc:  # noqa: BLE001
            report.errors.append(f"hashtag linking failed: {exc}")

    async def _ingest_mentions(
        self, posts: list[Post], post_id_map: dict[str, str], report: IngestionReport
    ) -> None:
        for post in posts:
            persisted_post_id = post_id_map.get(str(post.id))
            if not persisted_post_id or not post.mentions:
                continue
            try:
                existing = await self.mention_repo.by_post(persisted_post_id)
                existing_usernames = {m.username for m in existing}
                new_mentions = [
                    Mention(post_id=persisted_post_id, username=username)
                    for username in post.mentions
                    if username not in existing_usernames
                ]
                created = await self.mention_repo.bulk_create_mentions(new_mentions)
                report.mentions_created += len(created)
            except Exception as exc:  # noqa: BLE001
                report.errors.append(
                    f"mention ingestion failed for post {persisted_post_id}: {exc}"
                )

    async def _ingest_engagement(
        self, posts: list[Post], post_id_map: dict[str, str], report: IngestionReport
    ) -> None:
        for post in posts:
            persisted_post_id = post_id_map.get(str(post.id))
            if not persisted_post_id:
                continue
            try:
                normalizer = NORMALIZERS[post.platform]
                engagement = normalizer.extract_engagement(post).model_copy(
                    update={"post_id": persisted_post_id}
                )
                await self.engagement_repo.upsert_for_post(engagement)
                report.engagement_upserted += 1
            except Exception as exc:  # noqa: BLE001
                report.errors.append(
                    f"engagement upsert failed for post {persisted_post_id}: {exc}"
                )

    async def _generate_embeddings(
        self,
        posts: list[Post],
        comments: list[Any],
        videos: list[Any],
        report: IngestionReport,
    ) -> None:
        items: list[EmbeddableItem] = []
        for post in posts:
            text = post.caption or post.content or ""
            if text.strip():
                items.append(
                    EmbeddableItem(
                        source_type=EmbeddingSourceType.POST,
                        source_id=str(post.id),
                        platform=post.platform,
                        text=text,
                    )
                )
        for comment in comments:
            if comment.content.strip():
                items.append(
                    EmbeddableItem(
                        source_type=EmbeddingSourceType.COMMENT,
                        source_id=str(comment.id),
                        platform=comment.platform,
                        text=comment.content,
                    )
                )
        for video in videos:
            if video.transcript and video.transcript.strip():
                items.append(
                    EmbeddableItem(
                        source_type=EmbeddingSourceType.TRANSCRIPT,
                        source_id=str(video.id),
                        platform=video.platform,
                        text=video.transcript,
                    )
                )
        if not items:
            return
        try:
            report.embeddings_generated = await self.embedding_service.embed_batch(items)
        except Exception as exc:  # noqa: BLE001 - embedding failures never block persisted content
            report.errors.append(f"embedding generation failed: {exc}")

    async def _safe_bulk(
        self,
        fn: Any,
        items: list[ModelT],
        report: IngestionReport,
        label: str,
    ) -> list[ModelT]:
        if not items:
            return []
        try:
            return await fn(items)
        except Exception as exc:  # noqa: BLE001 - one bad batch shouldn't abort the whole run
            report.errors.append(f"{label} batch upsert failed: {exc}")
            logger.warning("Batch upsert failed", entity=label, error=str(exc), count=len(items))
            return []
