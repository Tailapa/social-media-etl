"""YouTube scraper: maps YouTube-specific Apify actor conventions onto the
platform-agnostic `BaseScraper` interface so `app/ingestion` and
`app/services` can treat YouTube exactly like every other platform.

YouTube is the one platform where a single raw item produces two distinct
domain rows for the same real-world "video": a `Post` (so it slots into the
unified content/retrieval model every other platform's content uses) and a
`Video` (so duration/transcript-specific fields have a home that doesn't
pollute the generic `Post` schema — see `app/models/pydantic/channel.py`).
Likewise every video's embedded channel info produces both an `Author`
(generic profile) and a `Channel` (subscriber-count semantics).

`streamers/youtube-scraper` handles channel/video listing, so both
`scrape_profile` and `scrape_posts` run that one actor with a different
`startUrls` target (a channel's landing page vs. its `/videos` tab).
Comments come from the separate `streamers/youtube-comments-scraper`, and
transcripts (best-effort, not guaranteed available) from
`pintostudio/youtube-transcript-scraper`.

YouTube has no hashtag/keyword search via these actors, so `scrape_hashtag`
and `scrape_keyword` are intentionally left unimplemented — the base class's
`NotImplementedError` default is correct here and is not overridden.
"""

from __future__ import annotations

from app.apify import register_scraper
from app.apify.base.scraper import BaseScraper, ScrapeResult
from app.config import get_settings
from app.logging import get_logger
from app.models.pydantic import Author, Channel
from app.models.pydantic.enums import PlatformName
from app.normalization import get_or_register
from app.normalization.youtube import (
    normalize_author,
    normalize_channel,
    normalize_comment,
    normalize_post,
    normalize_transcript_items,
    normalize_video,
)

logger = get_logger(__name__)

# Above this many videos, skipping per-video transcript fetches keeps a
# "scrape recent posts" call fast -- each transcript is its own actor run,
# and transcripts are a "when available" bonus, not a required field.
_TRANSCRIPT_FETCH_LIMIT = 10


@register_scraper(PlatformName.YOUTUBE)
class YouTubeScraper(BaseScraper):
    """`BaseScraper` implementation backed by the `streamers/*` YouTube actor
    family plus an optional transcript actor.
    """

    platform = "youtube"

    async def scrape_profile(self, identifier: str) -> ScrapeResult:
        """Scrape a single channel's metadata via `apify_youtube_scraper_actor`.

        `startUrls` (a list of `{"url": ...}` objects) is the standard input
        shape for `streamers/youtube-scraper`; pointing it at the channel's
        landing page and capping `maxResults` at 1 is enough to get one video
        item with the channel's info embedded, which is all `normalize_author`
        / `normalize_channel` need.
        """
        settings = get_settings()
        items = await self.runner.run_and_fetch(
            settings.apify_youtube_scraper_actor,
            {"startUrls": [{"url": f"https://www.youtube.com/{identifier}"}], "maxResults": 1},
        )
        if not items:
            return ScrapeResult(raw_item_count=0)

        raw = items[0]
        author = normalize_author(raw)
        channel = normalize_channel(raw, author_id=str(author.id))
        return ScrapeResult(authors=[author], channels=[channel], raw_item_count=len(items))

    async def scrape_posts(self, identifier: str, *, limit: int = 50) -> ScrapeResult:
        """Scrape recent videos for a channel via `apify_youtube_scraper_actor`.

        Pointing `startUrls` at the channel's `/videos` tab (rather than its
        landing page) is what makes the actor list multiple videos instead of
        just the channel's featured content.

        Each raw item becomes an `Author` + `Channel` (deduped across items,
        since every video from the same channel repeats the same embedded
        channel info) plus a `Post` + `Video` pair for the video itself.
        """
        settings = get_settings()
        items = await self.runner.run_and_fetch(
            settings.apify_youtube_scraper_actor,
            {
                "startUrls": [{"url": f"https://www.youtube.com/{identifier}/videos"}],
                "maxResults": limit,
            },
        )

        posts = []
        videos = []
        authors_by_key: dict[str, Author] = {}
        channels_by_key: dict[str, Channel] = {}
        for raw in items:
            try:
                author = get_or_register(
                    authors_by_key, normalize_author(raw), lambda a: a.dedup_key
                )
                channel = get_or_register(
                    channels_by_key,
                    normalize_channel(raw, author_id=str(author.id)),
                    lambda c: c.dedup_key,
                )
                post = normalize_post(raw, author_id=str(author.id))
                video = normalize_video(raw, channel_id=str(channel.id), post_id=str(post.id))
            except Exception:
                logger.warning("Skipping malformed YouTube video item", exc_info=True)
                continue
            posts.append(post)

            # Fetching a transcript is a separate actor run per video, so it's
            # only attempted for small pulls -- doing it for a 50-video batch
            # would multiply run count (and wall-clock time) by 50 for a
            # field that's a nice-to-have, not required by any downstream
            # consumer.
            if limit <= _TRANSCRIPT_FETCH_LIMIT and video.video_url:
                try:
                    transcript_items = await self.runner.run_and_fetch(
                        settings.apify_youtube_transcript_actor,
                        {"videoUrl": video.video_url},
                    )
                    text = normalize_transcript_items(transcript_items)
                    if text:
                        video = video.model_copy(update={"transcript": text})
                except Exception:
                    logger.warning(
                        "Transcript fetch failed, continuing without it",
                        video_url=video.video_url,
                        exc_info=True,
                    )

            videos.append(video)

        return ScrapeResult(
            posts=posts,
            authors=list(authors_by_key.values()),
            channels=list(channels_by_key.values()),
            videos=videos,
            raw_item_count=len(items),
        )

    async def scrape_comments(self, post_url_or_id: str, *, limit: int = 100) -> ScrapeResult:
        """Scrape comments (and replies) for a video via `apify_youtube_comment_actor`.

        `startUrls` + `maxComments` is the standard input shape for
        `streamers/youtube-comments-scraper`. The caller may pass either a
        full video URL or a bare video id. We don't have the video's real
        (DB) id here, so `post_url_or_id` is passed through as `post_id` --
        the ingestion pipeline remaps it to the persisted post id via
        `dedup_key`.
        """
        settings = get_settings()
        url = (
            post_url_or_id
            if post_url_or_id.startswith("http")
            else f"https://www.youtube.com/watch?v={post_url_or_id}"
        )
        items = await self.runner.run_and_fetch(
            settings.apify_youtube_comment_actor,
            {"startUrls": [{"url": url}], "maxComments": limit},
        )

        comments = []
        authors_by_key: dict[str, Author] = {}
        for item in items:
            try:
                author = get_or_register(
                    authors_by_key, normalize_author(item), lambda a: a.dedup_key
                )
                comment = normalize_comment(item, post_id=post_url_or_id, author_id=str(author.id))
            except Exception:
                logger.warning("Skipping malformed YouTube comment item", exc_info=True)
                continue
            comments.append(comment)

        return ScrapeResult(
            comments=comments, authors=list(authors_by_key.values()), raw_item_count=len(items)
        )
