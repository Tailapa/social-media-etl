from __future__ import annotations

import asyncio
from typing import Any

from app.models.pydantic import Comment
from app.repositories.base import BaseRepository


class CommentRepository(BaseRepository[Comment]):
    table_name = "comments"
    model = Comment

    async def get_by_platform_comment_id(
        self, platform: str, platform_comment_id: str
    ) -> Comment | None:
        def _run() -> Any:
            return (
                self._table.select("*")
                .eq("platform", platform)
                .eq("platform_comment_id", platform_comment_id)
                .limit(1)
                .execute()
            )

        response = await asyncio.to_thread(_run)
        rows = response.data
        return self._deserialize(rows[0]) if rows else None

    async def upsert_comment(self, comment: Comment) -> Comment:
        return await self.upsert(comment, on_conflict="platform,platform_comment_id")

    async def bulk_upsert_comments(self, comments: list[Comment]) -> list[Comment]:
        return await self.bulk_upsert(comments, on_conflict="platform,platform_comment_id")

    async def by_post(self, post_id: str, *, limit: int = 200) -> list[Comment]:
        return await self.list_all(filters={"post_id": post_id}, limit=limit)

    async def replies_to(self, parent_comment_id: str, *, limit: int = 200) -> list[Comment]:
        return await self.list_all(filters={"parent_comment_id": parent_comment_id}, limit=limit)
