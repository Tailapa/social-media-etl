"""Orchestrates "scrape a target, then ingest it" — the one call site that
bridges `app.apify` (scraping) and `app.ingestion` (persistence), used by
`scripts/run_scrape.py` and any future admin UI action.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Literal

from app.apify import get_scraper
from app.config import get_settings
from app.ingestion.pipeline import IngestionPipeline, IngestionReport
from app.logging import get_logger
from app.models.pydantic.enums import PlatformName

logger = get_logger(__name__)

ScrapeMode = Literal["profile", "posts", "comments", "hashtag", "keyword"]


@dataclass(slots=True, frozen=True)
class ScrapeTask:
    """One unit of work for `ScrapeService.scrape_many`."""

    platform: PlatformName | str
    mode: ScrapeMode
    target: str
    limit: int = 50


class ScrapeService:
    def __init__(
        self, pipeline: IngestionPipeline | None = None, *, max_concurrency: int | None = None
    ) -> None:
        self.pipeline = pipeline or IngestionPipeline()
        self._max_concurrency = max_concurrency or get_settings().max_concurrent_scrapes

    async def scrape_profile(
        self, platform: PlatformName | str, identifier: str
    ) -> IngestionReport:
        scraper = get_scraper(platform)
        result = await scraper.scrape_profile(identifier)
        return await self.pipeline.ingest(
            result, platform=str(platform), job_type="profile", target=identifier
        )

    async def scrape_posts(
        self, platform: PlatformName | str, identifier: str, *, limit: int = 50
    ) -> IngestionReport:
        scraper = get_scraper(platform)
        result = await scraper.scrape_posts(identifier, limit=limit)
        return await self.pipeline.ingest(
            result, platform=str(platform), job_type="posts", target=identifier
        )

    async def scrape_comments(
        self, platform: PlatformName | str, post_url_or_id: str, *, limit: int = 100
    ) -> IngestionReport:
        scraper = get_scraper(platform)
        result = await scraper.scrape_comments(post_url_or_id, limit=limit)
        return await self.pipeline.ingest(
            result, platform=str(platform), job_type="comments", target=post_url_or_id
        )

    async def scrape_hashtag(
        self, platform: PlatformName | str, hashtag: str, *, limit: int = 50
    ) -> IngestionReport:
        scraper = get_scraper(platform)
        result = await scraper.scrape_hashtag(hashtag, limit=limit)
        return await self.pipeline.ingest(
            result, platform=str(platform), job_type="hashtag", target=hashtag
        )

    async def scrape_keyword(
        self, platform: PlatformName | str, keyword: str, *, limit: int = 50
    ) -> IngestionReport:
        scraper = get_scraper(platform)
        result = await scraper.scrape_keyword(keyword, limit=limit)
        return await self.pipeline.ingest(
            result, platform=str(platform), job_type="keyword", target=keyword
        )

    async def scrape_many(self, tasks: list[ScrapeTask]) -> list[IngestionReport]:
        """Run several scrape tasks concurrently, bounded by
        `settings.max_concurrent_scrapes` (default 5) so a large batch of
        targets doesn't open unbounded simultaneous Apify actor runs.
        """
        semaphore = asyncio.Semaphore(self._max_concurrency)

        async def _run(task: ScrapeTask) -> IngestionReport:
            method = getattr(self, f"scrape_{task.mode}")
            kwargs = {} if task.mode == "profile" else {"limit": task.limit}
            async with semaphore:
                return await method(task.platform, task.target, **kwargs)

        return await asyncio.gather(*(_run(task) for task in tasks))
