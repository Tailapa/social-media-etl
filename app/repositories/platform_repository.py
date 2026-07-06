from __future__ import annotations

from app.models.pydantic import Platform
from app.repositories.base import BaseRepository


class PlatformRepository(BaseRepository[Platform]):
    table_name = "platforms"
    model = Platform

    async def get_by_name(self, name: str) -> Platform | None:
        results = await self.list_all(filters={"name": name}, limit=1)
        return results[0] if results else None
