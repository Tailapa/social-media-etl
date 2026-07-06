from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Any

from app.models.pydantic import Post
from app.repositories.base import BaseRepository


class PostRepository(BaseRepository[Post]):
    table_name = "posts"
    model = Post

    async def get_by_platform_post_id(self, platform: str, platform_post_id: str) -> Post | None:
        def _run() -> Any:
            return (
                self._table.select("*")
                .eq("platform", platform)
                .eq("platform_post_id", platform_post_id)
                .limit(1)
                .execute()
            )

        response = await asyncio.to_thread(_run)
        rows = response.data
        return self._deserialize(rows[0]) if rows else None

    async def upsert_post(self, post: Post) -> Post:
        return await self.upsert(post, on_conflict="platform,platform_post_id")

    async def bulk_upsert_posts(self, posts: list[Post]) -> list[Post]:
        return await self.bulk_upsert(posts, on_conflict="platform,platform_post_id")

    async def by_platform(self, platform: str, *, limit: int = 100, offset: int = 0) -> list[Post]:
        return await self.list_all(
            filters={"platform": platform},
            order_by="posted_at",
            descending=True,
            limit=limit,
            offset=offset,
        )

    async def by_author(self, author_id: str, *, limit: int = 100) -> list[Post]:
        return await self.list_all(
            filters={"author_id": author_id}, order_by="posted_at", descending=True, limit=limit
        )

    async def posted_between(
        self, start: datetime, end: datetime, *, platform: str | None = None, limit: int = 200
    ) -> list[Post]:
        def _run() -> Any:
            query = (
                self._table.select("*")
                .is_("deleted_at", "null")
                .gte("posted_at", start.isoformat())
                .lte("posted_at", end.isoformat())
            )
            if platform:
                query = query.eq("platform", platform)
            return query.order("posted_at", desc=True).limit(limit).execute()

        response = await asyncio.to_thread(_run)
        return [self._deserialize(row) for row in response.data]
