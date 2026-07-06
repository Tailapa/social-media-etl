from __future__ import annotations

import asyncio
from typing import Any

from app.models.pydantic import Channel, Video
from app.repositories.base import BaseRepository


class ChannelRepository(BaseRepository[Channel]):
    table_name = "channels"
    model = Channel

    async def get_by_platform_channel_id(
        self, platform: str, platform_channel_id: str
    ) -> Channel | None:
        def _run() -> Any:
            return (
                self._table.select("*")
                .eq("platform", platform)
                .eq("platform_channel_id", platform_channel_id)
                .limit(1)
                .execute()
            )

        response = await asyncio.to_thread(_run)
        rows = response.data
        return self._deserialize(rows[0]) if rows else None

    async def upsert_channel(self, channel: Channel) -> Channel:
        return await self.upsert(channel, on_conflict="platform,platform_channel_id")

    async def bulk_upsert_channels(self, channels: list[Channel]) -> list[Channel]:
        return await self.bulk_upsert(channels, on_conflict="platform,platform_channel_id")

    async def by_author(self, author_id: str) -> list[Channel]:
        return await self.list_all(filters={"author_id": author_id}, limit=50)


class VideoRepository(BaseRepository[Video]):
    table_name = "videos"
    model = Video

    async def get_by_platform_video_id(self, platform: str, platform_video_id: str) -> Video | None:
        def _run() -> Any:
            return (
                self._table.select("*")
                .eq("platform", platform)
                .eq("platform_video_id", platform_video_id)
                .limit(1)
                .execute()
            )

        response = await asyncio.to_thread(_run)
        rows = response.data
        return self._deserialize(rows[0]) if rows else None

    async def upsert_video(self, video: Video) -> Video:
        return await self.upsert(video, on_conflict="platform,platform_video_id")

    async def bulk_upsert_videos(self, videos: list[Video]) -> list[Video]:
        return await self.bulk_upsert(videos, on_conflict="platform,platform_video_id")

    async def by_channel(self, channel_id: str, *, limit: int = 100) -> list[Video]:
        return await self.list_all(
            filters={"channel_id": channel_id},
            order_by="published_at",
            descending=True,
            limit=limit,
        )
