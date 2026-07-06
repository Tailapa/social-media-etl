from __future__ import annotations

from app.models.pydantic import Engagement
from app.repositories.base import BaseRepository


class EngagementRepository(BaseRepository[Engagement]):
    table_name = "engagement"
    model = Engagement

    async def get_by_post(self, post_id: str) -> Engagement | None:
        results = await self.list_all(filters={"post_id": post_id}, limit=1)
        return results[0] if results else None

    async def upsert_for_post(self, engagement: Engagement) -> Engagement:
        return await self.upsert(engagement, on_conflict="post_id")

    async def top_by_likes(self, *, limit: int = 10) -> list[Engagement]:
        return await self.list_all(
            filters=None, order_by="likes", descending=True, limit=limit, include_deleted=True
        )
