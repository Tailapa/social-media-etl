from __future__ import annotations

import asyncio

from app.models.pydantic import Media
from app.repositories.base import BaseRepository


class MediaRepository(BaseRepository[Media]):
    table_name = "media"
    model = Media

    async def by_post(self, post_id: str) -> list[Media]:
        return await self.list_all(filters={"post_id": post_id}, limit=500)

    async def bulk_create_media(self, media_items: list[Media]) -> list[Media]:
        if not media_items:
            return []

        def _run() -> list[dict]:
            payload = [self._serialize(m) for m in media_items]
            return self._table.insert(payload).execute().data

        rows = await asyncio.to_thread(_run)
        return [self._deserialize(row) for row in rows]
