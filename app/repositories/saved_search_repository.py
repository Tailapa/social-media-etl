from __future__ import annotations

from app.models.pydantic import SavedSearch
from app.repositories.base import BaseRepository


class SavedSearchRepository(BaseRepository[SavedSearch]):
    table_name = "saved_searches"
    model = SavedSearch

    async def by_kind(self, kind: str, *, limit: int = 100) -> list[SavedSearch]:
        return await self.list_all(
            filters={"kind": kind}, order_by="created_at", descending=True, limit=limit
        )

    async def all_saved(self, *, limit: int = 200) -> list[SavedSearch]:
        return await self.list_all(order_by="created_at", descending=True, limit=limit)
