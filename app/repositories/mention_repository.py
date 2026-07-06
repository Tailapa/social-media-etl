from __future__ import annotations

import asyncio

from app.models.pydantic import Mention
from app.repositories.base import BaseRepository


class MentionRepository(BaseRepository[Mention]):
    table_name = "mentions"
    model = Mention

    async def by_post(self, post_id: str) -> list[Mention]:
        return await self.list_all(filters={"post_id": post_id}, limit=500)

    async def by_comment(self, comment_id: str) -> list[Mention]:
        return await self.list_all(filters={"comment_id": comment_id}, limit=500)

    async def by_username(self, username: str, *, limit: int = 100) -> list[Mention]:
        return await self.list_all(filters={"username": username.lstrip("@").lower()}, limit=limit)

    async def bulk_create_mentions(self, mentions: list[Mention]) -> list[Mention]:
        if not mentions:
            return []

        def _run() -> list[dict]:
            payload = [self._serialize(m) for m in mentions]
            return self._table.insert(payload).execute().data

        rows = await asyncio.to_thread(_run)
        return [self._deserialize(row) for row in rows]
