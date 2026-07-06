from __future__ import annotations

from app.models.pydantic import AssistantLog, QueryLog
from app.repositories.base import BaseRepository


class QueryLogRepository(BaseRepository[QueryLog]):
    table_name = "query_logs"
    model = QueryLog

    async def by_conversation(self, conversation_id: str, *, limit: int = 100) -> list[QueryLog]:
        return await self.list_all(
            filters={"conversation_id": conversation_id}, limit=limit, include_deleted=True
        )

    async def recent(self, *, limit: int = 50) -> list[QueryLog]:
        return await self.list_all(
            order_by="created_at", descending=True, limit=limit, include_deleted=True
        )


class AssistantLogRepository(BaseRepository[AssistantLog]):
    table_name = "assistant_logs"
    model = AssistantLog

    async def by_conversation(
        self, conversation_id: str, *, limit: int = 100
    ) -> list[AssistantLog]:
        return await self.list_all(
            filters={"conversation_id": conversation_id}, limit=limit, include_deleted=True
        )

    async def failures(self, *, limit: int = 50) -> list[AssistantLog]:
        def _has_error(log: AssistantLog) -> bool:
            return bool(log.error)

        logs = await self.list_all(
            order_by="created_at", descending=True, limit=limit, include_deleted=True
        )
        return [log for log in logs if _has_error(log)]
