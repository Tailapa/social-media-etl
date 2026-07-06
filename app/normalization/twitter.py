"""Maps raw Apify X/Twitter actor output (apidojo/tweet-scraper shape) into
unified Pydantic models. Field access goes through `first_present` for the
same reason as `app.normalization.instagram`: actor field naming drifts
across versions and this project should degrade gracefully, not crash.
"""

from __future__ import annotations

from datetime import datetime

from app.models.pydantic import Author, Comment, Engagement, Media, Post
from app.models.pydantic.enums import ContentType, MediaType, PlatformName
from app.normalization.common import as_int, first_present
from app.utils.text import extract_hashtags, extract_mentions, extract_urls


def _parse_timestamp(value: str | None) -> datetime | None:
    if not value:
        return None
    for fmt in (None, "%a %b %d %H:%M:%S %z %Y"):
        try:
            if fmt is None:
                return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
            return datetime.strptime(value, fmt)
        except ValueError:
            continue
    return None


def normalize_author(raw: dict) -> Author:
    """Map a tweet's embedded `author` object (or a standalone profile
    payload) to `Author`.
    """
    author_raw = raw.get("author", raw)
    return Author(
        platform=PlatformName.TWITTER,
        platform_user_id=str(first_present(author_raw, "id", "userId", "restId", default="")),
        username=str(
            first_present(author_raw, "userName", "username", "screen_name", default="unknown")
        ),
        display_name=first_present(author_raw, "name", "displayName"),
        bio=first_present(author_raw, "description", "bio"),
        profile_url=f"https://x.com/{first_present(author_raw, 'userName', 'username', default='')}",
        avatar_url=first_present(author_raw, "profilePicture", "profileImageUrl"),
        is_verified=bool(
            first_present(author_raw, "isVerified", "isBlueVerified", "verified", default=False)
        ),
        follower_count=as_int(first_present(author_raw, "followers", "followersCount")),
        following_count=as_int(first_present(author_raw, "following", "followingCount")),
        post_count=as_int(first_present(author_raw, "statusesCount", "tweetsCount")),
        location=author_raw.get("location"),
        external_url=author_raw.get("url"),
        platform_metadata={
            k: v for k, v in author_raw.items() if k not in {"userName", "username", "name"}
        },
    )


def normalize_post(raw: dict, *, author_id: str) -> Post:
    """Map a single tweet item to `Post`."""
    text = str(first_present(raw, "fullText", "text", default=""))
    entities = raw.get("entities") or {}
    hashtags = [
        h.get("text", h) if isinstance(h, dict) else h for h in entities.get("hashtags", [])
    ]
    mentions = [
        m.get("username", m) if isinstance(m, dict) else m for m in entities.get("mentions", [])
    ]
    urls = [
        u.get("expanded_url", u) if isinstance(u, dict) else u for u in entities.get("urls", [])
    ]

    content_type = ContentType.TWEET
    if first_present(raw, "isRetweet", default=False):
        content_type = ContentType.RETWEET
    elif first_present(raw, "isQuote", default=False):
        content_type = ContentType.QUOTE

    media_items: list[Media] = []
    for item in raw.get("media", []) or raw.get("extendedEntities", {}).get("media", []) or []:
        url = item.get("media_url_https") or item.get("mediaUrl") or item.get("url")
        if not url:
            continue
        media_type = (
            MediaType.VIDEO if item.get("type") in {"video", "animated_gif"} else MediaType.IMAGE
        )
        media_items.append(Media(post_id=None, media_type=media_type, url=url))

    return Post(
        platform=PlatformName.TWITTER,
        platform_post_id=str(first_present(raw, "id", "tweetId", default="")),
        author_id=author_id,
        content_type=content_type,
        caption=text,
        content=text,
        language=raw.get("lang"),
        url=first_present(raw, "url", "twitterUrl"),
        hashtags=hashtags or extract_hashtags(text),
        mentions=mentions or extract_mentions(text),
        urls=urls or extract_urls(text),
        media=media_items,
        posted_at=_parse_timestamp(first_present(raw, "createdAt", "created_at")),
        platform_metadata={
            "retweet_count": as_int(first_present(raw, "retweetCount", "retweet_count")),
            "reply_count": as_int(first_present(raw, "replyCount", "reply_count")),
            "like_count": as_int(first_present(raw, "likeCount", "favorite_count")),
            "quote_count": as_int(first_present(raw, "quoteCount", "quote_count")),
            "view_count": as_int(first_present(raw, "viewCount", "views")),
            "bookmark_count": as_int(raw.get("bookmarkCount")),
        },
    )


def normalize_comment(
    raw: dict, *, post_id: str, author_id: str, parent_id: str | None = None
) -> Comment:
    """Map a reply tweet (fetched via `conversation_id:{tweet_id}` search)
    to `Comment`.
    """
    text = str(first_present(raw, "fullText", "text", default=""))
    return Comment(
        platform=PlatformName.TWITTER,
        platform_comment_id=str(first_present(raw, "id", "tweetId", default="")),
        post_id=post_id,
        author_id=author_id,
        parent_comment_id=parent_id,
        content=text or "(no text)",
        language=raw.get("lang"),
        likes=as_int(first_present(raw, "likeCount", "favorite_count")),
        reply_count=as_int(first_present(raw, "replyCount", "reply_count")),
        hashtags=extract_hashtags(text),
        mentions=extract_mentions(text),
        posted_at=_parse_timestamp(first_present(raw, "createdAt", "created_at")),
        platform_metadata={"in_reply_to_id": raw.get("inReplyToId")},
    )


def extract_engagement(post: Post) -> Engagement:
    """Build an `Engagement` row from the counters normalize_post stashed in
    `platform_metadata` (X's "retweet" maps to the unified "shares" signal).
    """
    meta = post.platform_metadata
    return Engagement(
        likes=meta.get("like_count"),
        views=meta.get("view_count"),
        shares=meta.get("retweet_count"),
        comments_count=meta.get("reply_count"),
    )
