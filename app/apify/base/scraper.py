"""`BaseScraper` — the common interface every platform scraper implements.

Adding a new platform (Reddit, LinkedIn, TikTok, ...) means writing one class
that subclasses `BaseScraper` and implements the subset of these methods the
platform's Apify actors support; nothing in `app/ingestion`, `app/services`,
or the AI assistant needs to change (see docs/architecture.md).

Every method returns a `ScrapeResult` — never raw dicts — so callers never
branch on platform. Methods a given platform doesn't support (e.g. YouTube
has no "hashtag search") raise `NotImplementedError` with a clear message
rather than silently returning nothing.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field

from app.apify.base.client import ApifyActorRunner
from app.models.pydantic import Author, Channel, Comment, Media, Post, Video


@dataclass(slots=True)
class ScrapeResult:
    """Container for everything one scrape call produced.

    Grouping posts/authors/comments/media/channels/videos together (instead
    of returning five separate lists from five separate calls) keeps the
    ingestion pipeline's "validate -> normalize -> dedupe -> persist" loop
    working over one object regardless of which scrape method produced it.
    """

    posts: list[Post] = field(default_factory=list)
    authors: list[Author] = field(default_factory=list)
    comments: list[Comment] = field(default_factory=list)
    media: list[Media] = field(default_factory=list)
    channels: list[Channel] = field(default_factory=list)
    videos: list[Video] = field(default_factory=list)
    raw_item_count: int = 0

    def merge(self, other: ScrapeResult) -> ScrapeResult:
        return ScrapeResult(
            posts=[*self.posts, *other.posts],
            authors=[*self.authors, *other.authors],
            comments=[*self.comments, *other.comments],
            media=[*self.media, *other.media],
            channels=[*self.channels, *other.channels],
            videos=[*self.videos, *other.videos],
            raw_item_count=self.raw_item_count + other.raw_item_count,
        )


class BaseScraper(ABC):
    """Common contract for every platform-specific scraper.

    Subclasses own the mapping from "platform concept" (profile, post,
    hashtag, keyword, comment) to the specific Apify actor(s) that back it,
    and delegate raw-item -> Pydantic conversion to `app.normalization`.
    """

    platform: str

    def __init__(self, runner: ApifyActorRunner | None = None) -> None:
        self.runner = runner or ApifyActorRunner()

    @abstractmethod
    async def scrape_profile(self, identifier: str) -> ScrapeResult:
        """Scrape a single profile/channel's metadata (no posts)."""

    @abstractmethod
    async def scrape_posts(self, identifier: str, *, limit: int = 50) -> ScrapeResult:
        """Scrape recent posts/videos/tweets for a profile/channel."""

    @abstractmethod
    async def scrape_comments(self, post_url_or_id: str, *, limit: int = 100) -> ScrapeResult:
        """Scrape comments (and their replies) for a single post/video/tweet."""

    async def scrape_hashtag(self, hashtag: str, *, limit: int = 50) -> ScrapeResult:
        raise NotImplementedError(f"{self.platform} does not support hashtag search")

    async def scrape_keyword(self, keyword: str, *, limit: int = 50) -> ScrapeResult:
        raise NotImplementedError(f"{self.platform} does not support keyword search")
