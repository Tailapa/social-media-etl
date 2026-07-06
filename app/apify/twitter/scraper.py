"""X/Twitter scraper: maps X/Twitter-specific Apify actor conventions onto
the platform-agnostic `BaseScraper` interface so `app/ingestion` and
`app/services` can treat X/Twitter exactly like every other platform.

Unlike Instagram (a separate actor per concept), `apidojo/tweet-scraper` is a
single, search-driven actor: every method below funnels through the same
`apify_twitter_scraper_actor` and differs only in the `searchTerms` (or
`twitterHandles`) it sends as `run_input`. Profiles, posts, hashtags, and
keywords are all just different search queries over the same tweet stream;
"comments" are simply reply tweets found via a `conversation_id:` search.
"""

from __future__ import annotations

from app.apify import register_scraper
from app.apify.base.scraper import BaseScraper, ScrapeResult
from app.config import get_settings
from app.logging import get_logger
from app.models.pydantic import Author
from app.models.pydantic.enums import PlatformName
from app.normalization import get_or_register
from app.normalization.twitter import normalize_author, normalize_comment, normalize_post

logger = get_logger(__name__)


def _bare_tweet_id(post_url_or_id: str) -> str:
    """Extract a bare tweet id from a full status URL, or pass an id through.

    `conversation_id:` search requires the numeric tweet id, but callers may
    pass a full `https://x.com/<user>/status/<id>` URL.
    """
    if "/status/" in post_url_or_id:
        return post_url_or_id.rsplit("/", 1)[-1].split("?", 1)[0]
    return post_url_or_id


@register_scraper(PlatformName.TWITTER)
class TwitterScraper(BaseScraper):
    """`BaseScraper` implementation backed by `apidojo/tweet-scraper`."""

    platform = "twitter"

    async def scrape_profile(self, identifier: str) -> ScrapeResult:
        """Scrape a single profile's metadata.

        `apidojo/tweet-scraper` is tweet-centric, not profile-centric, so
        rather than rely on the actor's `twitterHandles` profile-only mode
        (less reliable across actor versions), this runs a `from:<handle>`
        search for a single tweet and derives the `Author` from that tweet's
        embedded `author` object via `normalize_author`.
        """
        settings = get_settings()
        handle = identifier.lstrip("@")
        items = await self.runner.run_and_fetch(
            settings.apify_twitter_scraper_actor,
            {"searchTerms": [f"from:{handle}"], "maxItems": 1},
        )
        authors = [normalize_author(items[0])] if items else []
        return ScrapeResult(authors=authors, raw_item_count=len(items))

    async def scrape_posts(self, identifier: str, *, limit: int = 50) -> ScrapeResult:
        """Scrape recent tweets for a profile via a `from:<handle>` search."""
        settings = get_settings()
        handle = identifier.lstrip("@")
        items = await self.runner.run_and_fetch(
            settings.apify_twitter_scraper_actor,
            {"searchTerms": [f"from:{handle}"], "maxItems": limit},
        )

        posts = []
        authors_by_key: dict[str, Author] = {}
        for item in items:
            try:
                author = get_or_register(
                    authors_by_key, normalize_author(item), lambda a: a.dedup_key
                )
                posts.append(normalize_post(item, author_id=str(author.id)))
            except Exception:
                logger.warning("Skipping malformed tweet item", exc_info=True)
                continue

        return ScrapeResult(
            posts=posts, authors=list(authors_by_key.values()), raw_item_count=len(items)
        )

    async def scrape_comments(self, post_url_or_id: str, *, limit: int = 100) -> ScrapeResult:
        """Scrape replies to a tweet via a `conversation_id:` search.

        X/Twitter has no separate "comment" concept -- replies are just
        tweets whose `conversation_id` matches the original tweet's id. The
        original tweet itself can show up in the search results too, so it
        is skipped (identified by matching `post_url_or_id`/its bare id).
        `post_url_or_id` is passed through as-is as `post_id` -- the
        ingestion pipeline remaps it to the persisted post id via `dedup_key`.
        """
        settings = get_settings()
        tweet_id = _bare_tweet_id(post_url_or_id)
        items = await self.runner.run_and_fetch(
            settings.apify_twitter_scraper_actor,
            {"searchTerms": [f"conversation_id:{tweet_id}"], "maxItems": limit},
        )

        comments = []
        authors_by_key: dict[str, Author] = {}
        for item in items:
            raw_id = str(item.get("id", ""))
            if raw_id in (tweet_id, post_url_or_id):
                continue
            try:
                author = get_or_register(
                    authors_by_key, normalize_author(item), lambda a: a.dedup_key
                )
                comment = normalize_comment(
                    item,
                    post_id=post_url_or_id,
                    author_id=str(author.id),
                    parent_id=item.get("inReplyToId"),
                )
            except Exception:
                logger.warning("Skipping malformed tweet reply item", exc_info=True)
                continue
            comments.append(comment)

        return ScrapeResult(
            comments=comments, authors=list(authors_by_key.values()), raw_item_count=len(items)
        )

    async def scrape_hashtag(self, hashtag: str, *, limit: int = 50) -> ScrapeResult:
        """Scrape recent tweets for a hashtag via a `#tag` search term."""
        settings = get_settings()
        items = await self.runner.run_and_fetch(
            settings.apify_twitter_scraper_actor,
            {"searchTerms": [f"#{hashtag.lstrip('#')}"], "maxItems": limit},
        )

        posts = []
        authors_by_key: dict[str, Author] = {}
        for item in items:
            try:
                author = get_or_register(
                    authors_by_key, normalize_author(item), lambda a: a.dedup_key
                )
                posts.append(normalize_post(item, author_id=str(author.id)))
            except Exception:
                logger.warning("Skipping malformed tweet item", exc_info=True)
                continue

        return ScrapeResult(
            posts=posts, authors=list(authors_by_key.values()), raw_item_count=len(items)
        )

    async def scrape_keyword(self, keyword: str, *, limit: int = 50) -> ScrapeResult:
        """Scrape recent tweets matching a free-text keyword search term."""
        settings = get_settings()
        items = await self.runner.run_and_fetch(
            settings.apify_twitter_scraper_actor,
            {"searchTerms": [keyword], "maxItems": limit},
        )

        posts = []
        authors_by_key: dict[str, Author] = {}
        for item in items:
            try:
                author = get_or_register(
                    authors_by_key, normalize_author(item), lambda a: a.dedup_key
                )
                posts.append(normalize_post(item, author_id=str(author.id)))
            except Exception:
                logger.warning("Skipping malformed tweet item", exc_info=True)
                continue

        return ScrapeResult(
            posts=posts, authors=list(authors_by_key.values()), raw_item_count=len(items)
        )
