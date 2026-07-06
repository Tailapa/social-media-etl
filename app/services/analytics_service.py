"""Read-only aggregation queries backing the Gradio analytics dashboard.

Aggregations that PostgREST can't express directly (group-by counts) are
computed by fetching bounded result sets and reducing them in Python —
acceptable at dashboard scale (see `HashtagRepository.trending` for the same
pattern already used elsewhere in the repository layer).
"""

from __future__ import annotations

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


class AnalyticsService:
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
        self.comment_repo = comment_repo or CommentRepository()
        self.author_repo = author_repo or AuthorRepository()
        self.engagement_repo = engagement_repo or EngagementRepository()
        self.hashtag_repo = hashtag_repo or HashtagRepository()
        self.scrape_job_repo = scrape_job_repo or ScrapeJobRepository()
        self.query_log_repo = query_log_repo or QueryLogRepository()

    async def total_posts(self) -> int:
        return await self.post_repo.count()

    async def total_comments(self) -> int:
        return await self.comment_repo.count()

    async def platform_distribution(self) -> dict[str, int]:
        counts = await asyncio.gather(
            *(self.post_repo.count(filters={"platform": p.value}) for p in PlatformName)
        )
        return dict(zip((p.value for p in PlatformName), counts, strict=True))

    async def most_active_authors(self, *, limit: int = 10) -> list[Author]:
        return await self.author_repo.most_active(limit=limit)

    async def trending_hashtags(self, *, limit: int = 10) -> list[dict[str, Any]]:
        return await self.hashtag_repo.trending(limit=limit)

    async def top_engagement_posts(self, *, limit: int = 10) -> list[Engagement]:
        return await self.engagement_repo.top_by_likes(limit=limit)

    async def recent_scrape_jobs(self, *, limit: int = 20) -> list[ScrapeJob]:
        return await self.scrape_job_repo.recent(limit=limit)

    async def ai_query_stats(self, *, limit: int = 200) -> dict[str, Any]:
        logs = await self.query_log_repo.recent(limit=limit)
        latencies = [log.latency_ms for log in logs if log.latency_ms is not None]
        return {
            "total_queries": len(logs),
            "avg_latency_ms": round(sum(latencies) / len(latencies), 2) if latencies else None,
        }

    async def dashboard_summary(self) -> dict[str, Any]:
        """Everything the Gradio analytics tab needs, fetched concurrently."""
        (
            total_posts,
            total_comments,
            distribution,
            top_authors,
            hashtags,
            top_posts,
            jobs,
            ai_stats,
        ) = await asyncio.gather(
            self.total_posts(),
            self.total_comments(),
            self.platform_distribution(),
            self.most_active_authors(),
            self.trending_hashtags(),
            self.top_engagement_posts(),
            self.recent_scrape_jobs(),
            self.ai_query_stats(),
        )
        return {
            "total_posts": total_posts,
            "total_comments": total_comments,
            "platform_distribution": distribution,
            "most_active_authors": top_authors,
            "trending_hashtags": hashtags,
            "top_engagement_posts": top_posts,
            "recent_scrape_jobs": jobs,
            "ai_query_stats": ai_stats,
        }
