from __future__ import annotations

import asyncio
from typing import Any

from app.models.pydantic import Author
from app.repositories.base import BaseRepository


class AuthorRepository(BaseRepository[Author]):
    table_name = "authors"
    model = Author

    async def get_by_platform_user_id(self, platform: str, platform_user_id: str) -> Author | None:
        def _run() -> Any:
            return (
                self._table.select("*")
                .eq("platform", platform)
                .eq("platform_user_id", platform_user_id)
                .limit(1)
                .execute()
            )

        response = await asyncio.to_thread(_run)
        rows = response.data
        return self._deserialize(rows[0]) if rows else None

    async def upsert_author(self, author: Author) -> Author:
        """Merge-on-conflict by (platform, platform_user_id) — this is how
        duplicate authors scraped in separate runs get reconciled into one row.
        """
        return await self.upsert(author, on_conflict="platform,platform_user_id")

    async def bulk_upsert_authors(self, authors: list[Author]) -> list[Author]:
        return await self.bulk_upsert(authors, on_conflict="platform,platform_user_id")

    async def most_active(self, platform: str | None = None, limit: int = 10) -> list[Author]:
        filters = {"platform": platform} if platform else None
        return await self.list_all(
            filters=filters, order_by="post_count", descending=True, limit=limit
        )
