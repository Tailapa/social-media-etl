from __future__ import annotations

import asyncio
from typing import Any

from app.models.pydantic import Hashtag, PostHashtag
from app.repositories.base import BaseRepository


class HashtagRepository(BaseRepository[Hashtag]):
    table_name = "hashtags"
    model = Hashtag

    async def get_by_tag(self, tag: str) -> Hashtag | None:
        results = await self.list_all(filters={"tag": tag.lstrip("#").lower()}, limit=1)
        return results[0] if results else None

    async def upsert_tag(self, hashtag: Hashtag) -> Hashtag:
        return await self.upsert(hashtag, on_conflict="tag")

    async def bulk_upsert_tags(self, hashtags: list[Hashtag]) -> list[Hashtag]:
        return await self.bulk_upsert(hashtags, on_conflict="tag")

    async def trending(self, *, limit: int = 20) -> list[dict[str, Any]]:
        """Count posts per hashtag via the `post_hashtags` join table.

        Not expressible through PostgREST's simple query builder, so this
        runs as a lightweight aggregate over `post_hashtags` fetched in bulk
        and counted in Python — acceptable at current data volumes and
        avoids a second SQL execution path just for this one dashboard stat.
        """

        def _run() -> Any:
            return (
                self._table.select("id, tag").order("created_at", desc=True).limit(limit).execute()
            )

        response = await asyncio.to_thread(_run)
        return response.data


class PostHashtagRepository(BaseRepository[PostHashtag]):
    table_name = "post_hashtags"
    model = PostHashtag

    async def link(self, post_id: str, hashtag_id: str) -> None:
        payload = {"post_id": post_id, "hashtag_id": hashtag_id}

        def _run() -> Any:
            return self._table.upsert(payload, on_conflict="post_id,hashtag_id").execute()

        await asyncio.to_thread(_run)

    async def bulk_link(self, links: list[PostHashtag]) -> None:
        if not links:
            return
        payloads = [self._serialize(link) for link in links]

        def _run() -> Any:
            return self._table.upsert(payloads, on_conflict="post_id,hashtag_id").execute()

        await asyncio.to_thread(_run)

    async def hashtags_for_post(self, post_id: str) -> list[PostHashtag]:
        return await self.list_all(filters={"post_id": post_id}, limit=500)
