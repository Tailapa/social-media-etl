from __future__ import annotations

from app.models.pydantic import ChatMessage
from app.repositories.base import BaseRepository


class MessageRepository(BaseRepository[ChatMessage]):
    table_name = "messages"
    model = ChatMessage

    async def by_conversation(self, conversation_id: str, *, limit: int = 200) -> list[ChatMessage]:
        return await self.list_all(
            filters={"conversation_id": conversation_id},
            order_by="created_at",
            descending=False,
            limit=limit,
            include_deleted=True,
        )
