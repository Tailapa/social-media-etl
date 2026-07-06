from __future__ import annotations

import asyncio
from typing import Any

from app.models.pydantic import Conversation
from app.repositories.base import BaseRepository


class ConversationRepository(BaseRepository[Conversation]):
    table_name = "conversations"
    model = Conversation

    async def by_user(self, user_id: str, *, limit: int = 50) -> list[Conversation]:
        return await self.list_all(
            filters={"user_id": user_id}, order_by="updated_at", descending=True, limit=limit
        )

    async def search_by_title(self, query: str, *, limit: int = 20) -> list[Conversation]:
        """Fuzzy title search backing the Gradio "search conversations" feature."""

        def _run() -> Any:
            return (
                self._table.select("*")
                .is_("deleted_at", "null")
                .ilike("title", f"%{query}%")
                .order("updated_at", desc=True)
                .limit(limit)
                .execute()
            )

        response = await asyncio.to_thread(_run)
        return [self._deserialize(row) for row in response.data]

    async def archive(self, conversation_id: str) -> Conversation:
        return await self.update(conversation_id, {"is_archived": True})
