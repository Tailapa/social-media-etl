"""Maps raw Apify YouTube actor output (streamers/youtube-scraper,
streamers/youtube-comments-scraper, pintostudio/youtube-transcript-scraper)
into unified Pydantic models.

YouTube is the one platform where "author" and "channel" are the same
real-world entity but two different tables (see app/models/pydantic/channel.py
docstring) — `normalize_author` and `normalize_channel` are both derived from
the same raw video/channel payload.
"""

from __future__ import annotations

from datetime import datetime

from app.models.pydantic import Author, Channel, Comment, Engagement, Media, Post, Video
from app.models.pydantic.enums import ContentType, MediaType, PlatformName
from app.normalization.common import as_int, first_present
from app.utils.text import extract_hashtags, extract_mentions, extract_urls


def _parse_timestamp(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def _parse_duration(value: str | int | float | None) -> float | None:
    """Duration may arrive as seconds (number) or "HH:MM:SS" / "MM:SS" text."""
    if value is None:
        return None
    if isinstance(value, int | float):
        return float(value)
    parts = str(value).split(":")
    if not all(p.isdigit() for p in parts):
        return None
    seconds = 0.0
    for part in parts:
        seconds = seconds * 60 + int(part)
    return seconds


def normalize_author(raw: dict) -> Author:
    """Map a video item's embedded channel info to `Author` (the channel
    owner, normalized like every other platform's profile).
    """
    channel_id = str(first_present(raw, "channelId", "channelUrl", default=""))
    return Author(
        platform=PlatformName.YOUTUBE,
        platform_user_id=channel_id,
        username=str(first_present(raw, "channelName", "channelUsername", default="unknown")),
        display_name=raw.get("channelName"),
        bio=raw.get("channelDescription"),
        profile_url=raw.get("channelUrl") or f"https://www.youtube.com/channel/{channel_id}",
        avatar_url=raw.get("channelAvatarUrl"),
        is_verified=bool(first_present(raw, "channelIsVerified", default=False)),
        follower_count=as_int(first_present(raw, "numberOfSubscribers", "subscriberCount")),
        post_count=as_int(first_present(raw, "channelTotalVideos", "videoCount")),
        platform_metadata={"is_monetized": raw.get("isMonetized")},
    )


def normalize_channel(raw: dict, *, author_id: str) -> Channel:
    """Map a video item's embedded channel info to `Channel`."""
    return Channel(
        platform=PlatformName.YOUTUBE,
        platform_channel_id=str(first_present(raw, "channelId", "channelUrl", default="")),
        author_id=author_id,
        name=str(first_present(raw, "channelName", default="unknown")),
        description=raw.get("channelDescription"),
        subscriber_count=as_int(first_present(raw, "numberOfSubscribers", "subscriberCount")),
        video_count=as_int(first_present(raw, "channelTotalVideos", "videoCount")),
        total_views=as_int(first_present(raw, "channelTotalViews", "channelViewCount")),
        country=raw.get("channelLocation"),
        platform_metadata={"joined_date": raw.get("channelJoinedDate")},
    )


def normalize_post(raw: dict, *, author_id: str) -> Post:
    """Map a YouTube video item to `Post` (content_type=VIDEO/SHORT)."""
    description = str(first_present(raw, "text", "description", default=""))
    duration = _parse_duration(raw.get("duration"))
    content_type = (
        ContentType.SHORT if (duration is not None and duration <= 60) else ContentType.VIDEO
    )

    media: list[Media] = []
    thumbnail = raw.get("thumbnailUrl")
    video_url = first_present(raw, "url", "videoUrl")
    if video_url:
        media.append(
            Media(post_id=None, media_type=MediaType.VIDEO, url=video_url, thumbnail_url=thumbnail)
        )

    return Post(
        platform=PlatformName.YOUTUBE,
        platform_post_id=str(first_present(raw, "id", "videoId", default="")),
        author_id=author_id,
        content_type=content_type,
        caption=str(raw.get("title", "")),
        content=description,
        url=video_url,
        hashtags=raw.get("hashtags") or extract_hashtags(description),
        mentions=extract_mentions(description),
        urls=extract_urls(description),
        media=media,
        posted_at=_parse_timestamp(first_present(raw, "date", "uploadDate")),
        location=raw.get("location"),
        platform_metadata={
            "view_count": as_int(first_present(raw, "viewCount", "views")),
            "like_count": as_int(raw.get("likes")),
            "comments_count": as_int(raw.get("commentsCount")),
            "duration_seconds": duration,
        },
    )


def normalize_video(raw: dict, *, channel_id: str, post_id: str | None = None) -> Video:
    """Map a YouTube video item to `Video` (duration/transcript semantics)."""
    return Video(
        platform=PlatformName.YOUTUBE,
        platform_video_id=str(first_present(raw, "id", "videoId", default="")),
        channel_id=channel_id,
        post_id=post_id,
        title=str(raw.get("title", "")),
        description=str(first_present(raw, "text", "description", default="")),
        transcript=raw.get("transcript"),
        duration_seconds=_parse_duration(raw.get("duration")),
        thumbnail_url=raw.get("thumbnailUrl"),
        video_url=first_present(raw, "url", "videoUrl"),
        published_at=_parse_timestamp(first_present(raw, "date", "uploadDate")),
        platform_metadata={"view_count": as_int(first_present(raw, "viewCount", "views"))},
    )


def normalize_transcript_items(items: list[dict]) -> str:
    """Join transcript-actor line items (each with a `text` field) into a
    single plain-text transcript string.
    """
    lines = [str(item.get("text", "")).strip() for item in items if item.get("text")]
    return " ".join(lines).strip()


def normalize_comment(
    raw: dict, *, post_id: str, author_id: str, parent_id: str | None = None
) -> Comment:
    """Map a single YouTube comment/reply item to `Comment`."""
    text = str(first_present(raw, "text", "comment", default=""))
    return Comment(
        platform=PlatformName.YOUTUBE,
        platform_comment_id=str(first_present(raw, "id", "cid", default="")),
        post_id=post_id,
        author_id=author_id,
        parent_comment_id=parent_id or (raw.get("parentCommentId") if raw.get("isReply") else None),
        content=text or "(no text)",
        likes=as_int(first_present(raw, "voteCount", "likesCount")),
        reply_count=as_int(raw.get("replyCount")),
        hashtags=extract_hashtags(text),
        mentions=extract_mentions(text),
        posted_at=_parse_timestamp(raw.get("publishedAt")),
        platform_metadata={"author_display_name": raw.get("author")},
    )


__all__ = [
    "normalize_author",
    "normalize_channel",
    "normalize_post",
    "normalize_video",
    "normalize_comment",
    "normalize_transcript_items",
    "extract_engagement",
]


def extract_engagement(post: Post) -> Engagement:
    """Build an `Engagement` row from the counters normalize_post stashed in
    `platform_metadata` (YouTube exposes no "shares" signal via these actors).
    """
    meta = post.platform_metadata
    return Engagement(
        likes=meta.get("like_count"),
        views=meta.get("view_count"),
        comments_count=meta.get("comments_count"),
    )
