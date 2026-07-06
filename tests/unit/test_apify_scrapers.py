"""Unit tests for the concrete Apify platform scrapers.

Each scraper is exercised against a `FakeRunner` (an in-memory stand-in for
`ApifyActorRunner`) that records every `run_and_fetch` call and returns
canned raw-item dicts shaped like real Apify actor output for that platform.
No network calls are made; nothing in `app.apify.base.client` is touched
except via the "constructs without a runner" smoke tests, which only assert
construction doesn't raise (see module docstring notes below).
"""

from __future__ import annotations

from typing import Any

from app.apify.base.scraper import ScrapeResult
from app.apify.instagram.scraper import InstagramScraper
from app.apify.twitter.scraper import TwitterScraper
from app.apify.youtube.scraper import YouTubeScraper
from app.config import get_settings


class FakeRunner:
    """Records every `run_and_fetch` call and returns canned items.

    `items_by_actor` maps an actor id to the list of raw dicts that call
    should return; `default_items` is used for any actor id not present in
    that mapping (the common case of a scraper that only calls one actor).
    """

    def __init__(
        self,
        default_items: list[dict[str, Any]] | None = None,
        items_by_actor: dict[str, list[dict[str, Any]]] | None = None,
    ) -> None:
        self.default_items = default_items if default_items is not None else []
        self.items_by_actor = items_by_actor or {}
        self.calls: list[dict[str, Any]] = []

    async def run_and_fetch(
        self,
        actor_id: str,
        run_input: dict[str, Any],
        *,
        memory_mbytes: int | None = None,
        timeout_secs: int | None = None,
    ) -> list[dict[str, Any]]:
        self.calls.append(
            {
                "actor_id": actor_id,
                "run_input": run_input,
                "memory_mbytes": memory_mbytes,
                "timeout_secs": timeout_secs,
            }
        )
        return self.items_by_actor.get(actor_id, self.default_items)


# --------------------------------------------------------------------------
# Instagram raw item builders
# --------------------------------------------------------------------------


def _ig_post_item(
    *, post_id: str = "1", owner_id: str = "100", username: str = "ig_user", **overrides: Any
) -> dict[str, Any]:
    item = {
        "id": post_id,
        "shortCode": f"code{post_id}",
        "caption": "Hello #world",
        "ownerUsername": username,
        "ownerId": owner_id,
        "likesCount": 50,
        "commentsCount": 5,
        "timestamp": "2024-01-01T00:00:00.000Z",
        "url": f"https://www.instagram.com/p/code{post_id}/",
        "productType": "feed",
    }
    item.update(overrides)
    return item


def _ig_comment_item(
    *, comment_id: str = "c1", owner_id: str = "200", username: str = "commenter1", **overrides: Any
) -> dict[str, Any]:
    item = {
        "id": comment_id,
        "text": "Nice post!",
        "ownerUsername": username,
        "ownerId": owner_id,
        "likesCount": 2,
        "timestamp": "2024-01-01T00:00:00.000Z",
    }
    item.update(overrides)
    return item


# --------------------------------------------------------------------------
# Instagram
# --------------------------------------------------------------------------


class TestInstagramScraper:
    def test_platform_attribute(self) -> None:
        assert InstagramScraper.platform == "instagram"

    def test_constructs_without_runner(self) -> None:
        # ApifyActorRunner.__init__ only calls get_apify_client(), which
        # doesn't validate the token eagerly (ApifyClient just stores it) --
        # so construction with no credentials configured must not raise.
        scraper = InstagramScraper()
        assert scraper.platform == "instagram"

    async def test_scrape_posts_returns_result_and_dedupes_authors(self) -> None:
        items = [
            _ig_post_item(post_id="1", owner_id="100", username="ig_user"),
            _ig_post_item(post_id="2", owner_id="100", username="ig_user"),
        ]
        runner = FakeRunner(default_items=items)
        scraper = InstagramScraper(runner=runner)

        result = await scraper.scrape_posts("ig_user", limit=10)

        assert isinstance(result, ScrapeResult)
        assert len(result.posts) == 2
        assert len(result.authors) == 1  # deduped: same ownerId across both posts
        assert result.authors[0].username == "ig_user"
        assert result.raw_item_count == 2

        # Regression: every post must reference the *surviving* deduped
        # author's id. Normalizing a fresh Author per item and deduping
        # afterward would silently orphan posts pointing at a discarded
        # author's id, causing a real FK violation on persist.
        surviving_author_id = str(result.authors[0].id)
        assert all(str(post.author_id) == surviving_author_id for post in result.posts)

        settings = get_settings()
        assert runner.calls[0]["actor_id"] == settings.apify_instagram_post_actor
        assert runner.calls[0]["run_input"] == {"username": ["ig_user"], "resultsLimit": 10}

    async def test_scrape_comments_builds_comments_and_reply_links(self) -> None:
        top = _ig_comment_item(
            comment_id="c1",
            owner_id="200",
            username="commenter1",
            replies=[
                {
                    "id": "r1",
                    "text": "Totally agree",
                    "ownerUsername": "replier1",
                    "ownerId": "300",
                    "likesCount": 1,
                }
            ],
        )
        runner = FakeRunner(default_items=[top])
        scraper = InstagramScraper(runner=runner)

        result = await scraper.scrape_comments("shortcode123", limit=50)

        assert len(result.comments) == 2
        top_comment, reply_comment = result.comments
        assert top_comment.content == "Nice post!"
        assert top_comment.parent_comment_id is None
        assert reply_comment.content == "Totally agree"
        assert reply_comment.parent_comment_id == str(top_comment.id)
        assert len(result.authors) == 2  # commenter + replier, distinct owners

        settings = get_settings()
        assert runner.calls[0]["actor_id"] == settings.apify_instagram_comment_actor
        assert runner.calls[0]["run_input"] == {
            "postUrls": ["shortcode123"],
            "resultsLimit": 50,
        }

    async def test_scrape_hashtag_builds_posts_and_search_input(self) -> None:
        items = [_ig_post_item(post_id="9", owner_id="500", username="tagger")]
        runner = FakeRunner(default_items=items)
        scraper = InstagramScraper(runner=runner)

        result = await scraper.scrape_hashtag("#Sunset", limit=25)

        assert len(result.posts) == 1
        assert len(result.authors) == 1
        settings = get_settings()
        assert runner.calls[0]["actor_id"] == settings.apify_instagram_hashtag_actor
        assert runner.calls[0]["run_input"] == {"hashtags": ["Sunset"], "resultsLimit": 25}


# --------------------------------------------------------------------------
# Twitter raw item builders
# --------------------------------------------------------------------------


def _tw_tweet_item(
    *, tweet_id: str = "1", author_id: str = "u1", username: str = "tw_user", **overrides: Any
) -> dict[str, Any]:
    item = {
        "id": tweet_id,
        "fullText": "Hello world #test",
        "createdAt": "Mon Jan 01 00:00:00 +0000 2024",
        "author": {"id": author_id, "userName": username, "name": "TW User"},
        "url": f"https://x.com/{username}/status/{tweet_id}",
    }
    item.update(overrides)
    return item


class TestTwitterScraper:
    def test_platform_attribute(self) -> None:
        assert TwitterScraper.platform == "twitter"

    def test_constructs_without_runner(self) -> None:
        scraper = TwitterScraper()
        assert scraper.platform == "twitter"

    async def test_scrape_posts_returns_result_and_dedupes_authors(self) -> None:
        items = [
            _tw_tweet_item(tweet_id="1", author_id="u1", username="tw_user"),
            _tw_tweet_item(tweet_id="2", author_id="u1", username="tw_user"),
        ]
        runner = FakeRunner(default_items=items)
        scraper = TwitterScraper(runner=runner)

        result = await scraper.scrape_posts("tw_user", limit=20)

        assert isinstance(result, ScrapeResult)
        assert len(result.posts) == 2
        assert len(result.authors) == 1  # deduped: same author.id across both tweets
        assert result.authors[0].username == "tw_user"

        surviving_author_id = str(result.authors[0].id)
        assert all(str(post.author_id) == surviving_author_id for post in result.posts)

        settings = get_settings()
        assert runner.calls[0]["actor_id"] == settings.apify_twitter_scraper_actor
        assert runner.calls[0]["run_input"] == {
            "searchTerms": ["from:tw_user"],
            "maxItems": 20,
        }

    async def test_scrape_comments_skips_original_tweet_and_links_parent(self) -> None:
        original = _tw_tweet_item(tweet_id="1111", author_id="u1", username="op")
        reply = _tw_tweet_item(
            tweet_id="2222",
            author_id="u2",
            username="replier",
            inReplyToId="1111",
        )
        runner = FakeRunner(default_items=[original, reply])
        scraper = TwitterScraper(runner=runner)

        result = await scraper.scrape_comments("https://x.com/op/status/1111", limit=50)

        assert len(result.comments) == 1  # original tweet skipped
        comment = result.comments[0]
        assert comment.content == "Hello world #test"
        assert comment.parent_comment_id == "1111"

        settings = get_settings()
        assert runner.calls[0]["actor_id"] == settings.apify_twitter_scraper_actor
        assert runner.calls[0]["run_input"] == {
            "searchTerms": ["conversation_id:1111"],
            "maxItems": 50,
        }

    async def test_scrape_hashtag_builds_search_terms(self) -> None:
        items = [_tw_tweet_item(tweet_id="5", author_id="u5", username="tagger")]
        runner = FakeRunner(default_items=items)
        scraper = TwitterScraper(runner=runner)

        result = await scraper.scrape_hashtag("#python", limit=30)

        assert len(result.posts) == 1
        assert runner.calls[0]["run_input"] == {
            "searchTerms": ["#python"],
            "maxItems": 30,
        }

    async def test_scrape_keyword_builds_search_terms(self) -> None:
        items = [_tw_tweet_item(tweet_id="6", author_id="u6", username="keyworder")]
        runner = FakeRunner(default_items=items)
        scraper = TwitterScraper(runner=runner)

        result = await scraper.scrape_keyword("machine learning", limit=15)

        assert len(result.posts) == 1
        assert runner.calls[0]["run_input"] == {
            "searchTerms": ["machine learning"],
            "maxItems": 15,
        }


# --------------------------------------------------------------------------
# YouTube raw item builders
# --------------------------------------------------------------------------


def _yt_video_item(
    *,
    video_id: str = "vid1",
    channel_id: str = "chan1",
    channel_name: str = "Test Channel",
    **overrides: Any,
) -> dict[str, Any]:
    item = {
        "id": video_id,
        "title": "Test Video",
        "channelId": channel_id,
        "channelName": channel_name,
        "duration": "00:05:30",
        "date": "2024-01-01T00:00:00Z",
        "url": f"https://www.youtube.com/watch?v={video_id}",
        "viewCount": 1000,
    }
    item.update(overrides)
    return item


def _yt_comment_item(
    *, comment_id: str = "cm1", author_display_name: str = "Commenter", **overrides: Any
) -> dict[str, Any]:
    item = {
        "id": comment_id,
        "text": "Great video!",
        "author": author_display_name,
        "publishedAt": "2024-01-01T00:00:00Z",
        "voteCount": 3,
    }
    item.update(overrides)
    return item


class TestYouTubeScraper:
    def test_platform_attribute(self) -> None:
        assert YouTubeScraper.platform == "youtube"

    def test_constructs_without_runner(self) -> None:
        scraper = YouTubeScraper()
        assert scraper.platform == "youtube"

    async def test_scrape_profile_returns_author_and_channel(self) -> None:
        items = [_yt_video_item(video_id="vid1", channel_id="chan1")]
        runner = FakeRunner(default_items=items)
        scraper = YouTubeScraper(runner=runner)

        result = await scraper.scrape_profile("@testchannel")

        assert len(result.authors) == 1
        assert len(result.channels) == 1
        assert result.authors[0].username == "Test Channel"
        assert result.channels[0].name == "Test Channel"

        settings = get_settings()
        assert runner.calls[0]["actor_id"] == settings.apify_youtube_scraper_actor
        assert runner.calls[0]["run_input"] == {
            "startUrls": [{"url": "https://www.youtube.com/@testchannel"}],
            "maxResults": 1,
        }

    async def test_scrape_posts_populates_posts_videos_and_dedupes(self) -> None:
        # limit=50 (the default) keeps limit > _TRANSCRIPT_FETCH_LIMIT (10),
        # so no transcript actor call is made -- only the main listing actor.
        items = [
            _yt_video_item(video_id="vid1", channel_id="chan1"),
            _yt_video_item(video_id="vid2", channel_id="chan1"),
        ]
        runner = FakeRunner(default_items=items)
        scraper = YouTubeScraper(runner=runner)

        result = await scraper.scrape_posts("@testchannel", limit=50)

        assert isinstance(result, ScrapeResult)
        assert len(result.posts) == 2
        assert len(result.videos) == 2
        assert len(result.authors) == 1  # deduped: same channelId across both videos
        assert len(result.channels) == 1

        surviving_author_id = str(result.authors[0].id)
        surviving_channel_id = str(result.channels[0].id)
        assert all(str(post.author_id) == surviving_author_id for post in result.posts)
        assert all(str(video.channel_id) == surviving_channel_id for video in result.videos)

        settings = get_settings()
        assert runner.calls[0]["actor_id"] == settings.apify_youtube_scraper_actor
        assert runner.calls[0]["run_input"] == {
            "startUrls": [{"url": "https://www.youtube.com/@testchannel/videos"}],
            "maxResults": 50,
        }

    async def test_scrape_posts_fetches_transcript_when_under_limit(self) -> None:
        settings = get_settings()
        video_item = _yt_video_item(video_id="vid1", channel_id="chan1")
        transcript_items = [{"text": "hello"}, {"text": "world"}]
        runner = FakeRunner(
            items_by_actor={
                settings.apify_youtube_scraper_actor: [video_item],
                settings.apify_youtube_transcript_actor: transcript_items,
            }
        )
        scraper = YouTubeScraper(runner=runner)

        result = await scraper.scrape_posts("@testchannel", limit=5)

        assert len(result.videos) == 1
        assert result.videos[0].transcript == "hello world"
        actor_ids_called = [c["actor_id"] for c in runner.calls]
        assert settings.apify_youtube_transcript_actor in actor_ids_called

    async def test_scrape_comments_builds_comments_with_reply_linking(self) -> None:
        top = _yt_comment_item(comment_id="c1", author_display_name="Top Commenter")
        reply = _yt_comment_item(
            comment_id="c2",
            author_display_name="Replier",
            isReply=True,
            parentCommentId="c1",
        )
        runner = FakeRunner(default_items=[top, reply])
        scraper = YouTubeScraper(runner=runner)

        result = await scraper.scrape_comments("vid1", limit=100)

        assert len(result.comments) == 2
        top_comment, reply_comment = result.comments
        assert top_comment.parent_comment_id is None
        assert reply_comment.parent_comment_id == "c1"

        settings = get_settings()
        assert runner.calls[0]["actor_id"] == settings.apify_youtube_comment_actor
        assert runner.calls[0]["run_input"] == {
            "startUrls": [{"url": "https://www.youtube.com/watch?v=vid1"}],
            "maxComments": 100,
        }
