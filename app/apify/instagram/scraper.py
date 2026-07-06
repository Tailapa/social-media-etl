"""Instagram scraper: maps Instagram-specific Apify actor conventions
(separate actors for profile/post/hashtag/comment scraping, each with its
own input shape and raw item schema) onto the platform-agnostic
`BaseScraper` interface so `app/ingestion` and `app/services` can treat
Instagram exactly like every other platform.

Actor input keys vary across Apify's Instagram actor family and change
between actor versions; the shapes chosen below follow the most common
convention for each actor at the time this was written and are documented
inline where they could plausibly differ.
"""

from __future__ import annotations

from app.apify import register_scraper
from app.apify.base.scraper import BaseScraper, ScrapeResult
from app.config import get_settings
from app.logging import get_logger
from app.models.pydantic import Author
from app.models.pydantic.enums import PlatformName
from app.normalization import get_or_register
from app.normalization.instagram import normalize_author, normalize_comment, normalize_post

logger = get_logger(__name__)


@register_scraper(PlatformName.INSTAGRAM)
class InstagramScraper(BaseScraper):
    """`BaseScraper` implementation backed by Apify's Instagram actor family."""

    platform = "instagram"

    async def scrape_profile(self, identifier: str) -> ScrapeResult:
        """Scrape a single profile's metadata via `apify_instagram_profile_actor`.

        `usernames` is the standard input key for `apify/instagram-profile-scraper`.
        """
        settings = get_settings()
        items = await self.runner.run_and_fetch(
            settings.apify_instagram_profile_actor,
            {"usernames": [identifier]},
        )
        authors = [normalize_author(item) for item in items[:1]]
        return ScrapeResult(authors=authors, raw_item_count=len(items))

    async def scrape_posts(self, identifier: str, *, limit: int = 50) -> ScrapeResult:
        """Scrape recent posts for a profile via `apify_instagram_post_actor`.

        `username` is an array field for this actor (verified live against
        `apify/instagram-post-scraper` — it rejects a bare string with
        "Field input.username must be array").
        """
        settings = get_settings()
        items = await self.runner.run_and_fetch(
            settings.apify_instagram_post_actor,
            {"username": [identifier], "resultsLimit": limit},
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
                logger.warning("Skipping malformed Instagram post item", exc_info=True)
                continue

        return ScrapeResult(
            posts=posts, authors=list(authors_by_key.values()), raw_item_count=len(items)
        )

    async def scrape_comments(self, post_url_or_id: str, *, limit: int = 100) -> ScrapeResult:
        """Scrape comments (and replies) for a post via `apify_instagram_comment_actor`.

        `postUrls` + `resultsLimit` is the standard input shape for
        `apify/instagram-comment-scraper`. The caller may pass either a full
        post URL or a bare post id/shortcode; the actor itself handles both.
        We don't have the post's real (DB) id here, so `post_url_or_id` is
        passed through as `post_id` -- the ingestion pipeline remaps it to
        the persisted post id via `dedup_key`.
        """
        settings = get_settings()
        items = await self.runner.run_and_fetch(
            settings.apify_instagram_comment_actor,
            {"postUrls": [post_url_or_id], "resultsLimit": limit},
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
                logger.warning("Skipping malformed Instagram comment item", exc_info=True)
                continue
            comments.append(comment)

            for reply in item.get("replies") or item.get("childComments") or []:
                try:
                    reply_author = get_or_register(
                        authors_by_key, normalize_author(reply), lambda a: a.dedup_key
                    )
                    reply_comment = normalize_comment(
                        reply,
                        post_id=post_url_or_id,
                        author_id=str(reply_author.id),
                        parent_id=str(comment.id),
                    )
                except Exception:
                    logger.warning("Skipping malformed Instagram reply item", exc_info=True)
                    continue
                comments.append(reply_comment)

        return ScrapeResult(
            comments=comments, authors=list(authors_by_key.values()), raw_item_count=len(items)
        )

    async def scrape_hashtag(self, hashtag: str, *, limit: int = 50) -> ScrapeResult:
        """Scrape recent posts for a hashtag via `apify_instagram_hashtag_actor`.

        Each raw item is post-shaped, so this normalizes the same way as
        `scrape_posts`: one author + one post per item.
        """
        settings = get_settings()
        items = await self.runner.run_and_fetch(
            settings.apify_instagram_hashtag_actor,
            {"hashtags": [hashtag.lstrip("#")], "resultsLimit": limit},
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
                logger.warning("Skipping malformed Instagram hashtag item", exc_info=True)
                continue

        return ScrapeResult(
            posts=posts, authors=list(authors_by_key.values()), raw_item_count=len(items)
        )
