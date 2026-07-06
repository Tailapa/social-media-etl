"""Repository for `scrape_jobs` — tracks each ingestion pipeline run so
progress can be reported and resumed (see app/ingestion/pipeline.py).
"""

from __future__ import annotations

from datetime import UTC, datetime

from pydantic import Field

from app.models.pydantic.base import BaseSchema, IdentifiedMixin
from app.models.pydantic.enums import PlatformName, ScrapeJobStatus
from app.repositories.base import BaseRepository


class ScrapeJob(IdentifiedMixin, BaseSchema):
    """Mirrors the `scrape_jobs` table, which — unlike most tables in this
    schema — has no `updated_at` column (a job's lifecycle is tracked via
    `started_at`/`finished_at`/`status` instead), so this does not use
    `TimestampMixin`.
    """

    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    platform: PlatformName
    job_type: str
    status: ScrapeJobStatus = ScrapeJobStatus.PENDING
    target: str | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    records_scraped: int = 0
    error: str | None = None


class ScrapeJobRepository(BaseRepository[ScrapeJob]):
    table_name = "scrape_jobs"
    model = ScrapeJob

    async def start(self, platform: str, job_type: str, target: str | None = None) -> ScrapeJob:
        job = ScrapeJob(
            platform=PlatformName(platform),
            job_type=job_type,
            status=ScrapeJobStatus.RUNNING,
            target=target,
            started_at=datetime.now(UTC),
        )
        return await self.create(job)

    async def mark_succeeded(self, job_id: str, records_scraped: int) -> ScrapeJob:
        return await self.update(
            job_id,
            {
                "status": ScrapeJobStatus.SUCCEEDED.value,
                "finished_at": datetime.now(UTC).isoformat(),
                "records_scraped": records_scraped,
            },
        )

    async def mark_partial(self, job_id: str, records_scraped: int, error: str) -> ScrapeJob:
        return await self.update(
            job_id,
            {
                "status": ScrapeJobStatus.PARTIAL.value,
                "finished_at": datetime.now(UTC).isoformat(),
                "records_scraped": records_scraped,
                "error": error,
            },
        )

    async def mark_failed(self, job_id: str, error: str) -> ScrapeJob:
        return await self.update(
            job_id,
            {
                "status": ScrapeJobStatus.FAILED.value,
                "finished_at": datetime.now(UTC).isoformat(),
                "error": error,
            },
        )

    async def recent(self, *, platform: str | None = None, limit: int = 50) -> list[ScrapeJob]:
        filters = {"platform": platform} if platform else None
        return await self.list_all(
            filters=filters,
            order_by="created_at",
            descending=True,
            limit=limit,
            include_deleted=True,
        )
