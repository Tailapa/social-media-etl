"""Maps raw Apify Instagram actor output into unified Pydantic models.

Field names are read defensively via `first_present` because Apify's
Instagram actors (profile/post/hashtag/comment scrapers) have changed field
naming across versions (e.g. `commentsCount` vs `commentCount`) and this
project should keep working across actor upgrades without a code change.
"""

from __future__ import annotations

from datetime import datetime

from app.models.pydantic import Author, Comment, Engagement, Media, Post
from app.models.pydantic.enums import ContentType, MediaType, PlatformName
from app.normalization.common import as_int, first_present
from app.utils.text import extract_hashtags, extract_mentions, extract_urls

_CONTENT_TYPE_MAP = {
    "video": ContentType.REEL,
    "clips": ContentType.REEL,
    "igtv": ContentType.VIDEO,
    "sidecar": ContentType.POST,
    "image": ContentType.POST,
    "feed": ContentType.POST,
}


def _parse_timestamp(value: str | int | float | None) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, int | float):
        return datetime.fromtimestamp(value, tz=None)
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def normalize_author(raw: dict) -> Author:
    """Map an Instagram profile/post-owner payload to `Author`."""
    return Author(
        platform=PlatformName.INSTAGRAM,
        platform_user_id=str(
            first_present(raw, "ownerId", "id", "userId", "pk", default=raw.get("username", ""))
        ),
        username=str(first_present(raw, "ownerUsername", "username", default="unknown")),
        display_name=first_present(raw, "ownerFullName", "fullName", "full_name"),
        bio=first_present(raw, "biography", "bio"),
        profile_url=first_present(raw, "url", "profileUrl")
        or f"https://www.instagram.com/{first_present(raw, 'ownerUsername', 'username', default='')}/",
        avatar_url=first_present(raw, "profilePicUrl", "profilePicUrlHD"),
        is_verified=bool(first_present(raw, "verified", "isVerified", default=False)),
        is_private=bool(first_present(raw, "private", "isPrivate", default=False)),
        follower_count=as_int(first_present(raw, "followersCount", "followerCount")),
        following_count=as_int(first_present(raw, "followsCount", "followingCount")),
        post_count=as_int(first_present(raw, "postsCount", "postCount")),
        external_url=raw.get("externalUrl"),
        platform_metadata={
            k: v
            for k, v in raw.items()
            if k
            not in {
                "ownerUsername",
                "username",
                "ownerFullName",
                "fullName",
                "biography",
                "bio",
                "profilePicUrl",
            }
        },
    )


def normalize_post(raw: dict, *, author_id: str) -> Post:
    """Map a single Instagram post/reel item to `Post`."""
    caption = first_present(raw, "caption", "text", default="") or ""
    raw_type = str(first_present(raw, "productType", "type", default="feed")).lower()
    content_type = _CONTENT_TYPE_MAP.get(raw_type, ContentType.POST)

    media: list[Media] = []
    display_url = raw.get("displayUrl")
    if display_url:
        media.append(Media(post_id=None, media_type=MediaType.IMAGE, url=display_url))
    for child in raw.get("childPosts", []) or []:
        child_url = child.get("displayUrl") or child.get("videoUrl")
        if child_url:
            media.append(
                Media(
                    post_id=None,
                    media_type=MediaType.VIDEO if child.get("videoUrl") else MediaType.IMAGE,
                    url=child_url,
                )
            )
    video_url = raw.get("videoUrl")
    if video_url:
        media.append(Media(post_id=None, media_type=MediaType.VIDEO, url=video_url))

    return Post(
        platform=PlatformName.INSTAGRAM,
        platform_post_id=str(first_present(raw, "id", "shortCode", default="")),
        author_id=author_id,
        content_type=content_type,
        caption=caption,
        content=caption,
        url=first_present(raw, "url") or f"https://www.instagram.com/p/{raw.get('shortCode', '')}/",
        hashtags=raw.get("hashtags") or extract_hashtags(caption),
        mentions=raw.get("mentions") or extract_mentions(caption),
        urls=extract_urls(caption),
        media=media,
        posted_at=_parse_timestamp(first_present(raw, "timestamp", "takenAt")),
        is_sponsored=bool(first_present(raw, "isSponsored", default=False)),
        location=raw.get("locationName"),
        platform_metadata={
            "likes_count": as_int(first_present(raw, "likesCount", "likeCount")),
            "comments_count": as_int(first_present(raw, "commentsCount", "commentCount")),
            "video_view_count": as_int(raw.get("videoViewCount")),
            "video_play_count": as_int(raw.get("videoPlayCount")),
        },
    )


def normalize_comment(
    raw: dict, *, post_id: str, author_id: str, parent_id: str | None = None
) -> Comment:
    """Map a single Instagram comment/reply item to `Comment`."""
    text = str(first_present(raw, "text", "content", default=""))
    return Comment(
        platform=PlatformName.INSTAGRAM,
        platform_comment_id=str(first_present(raw, "id", "commentId", default="")),
        post_id=post_id,
        author_id=author_id,
        parent_comment_id=parent_id,
        content=text or "(no text)",
        likes=as_int(first_present(raw, "likesCount", "likeCount")),
        reply_count=as_int(first_present(raw, "repliesCount", "replyCount")),
        hashtags=extract_hashtags(text),
        mentions=extract_mentions(text),
        posted_at=_parse_timestamp(first_present(raw, "timestamp", "createdAt")),
        platform_metadata={"owner_username": first_present(raw, "ownerUsername", "username")},
    )


def extract_engagement(post: Post) -> Engagement:
    """Build an `Engagement` row from the counters normalize_post stashed in
    `platform_metadata` (Instagram exposes no "shares" signal).
    """
    meta = post.platform_metadata
    return Engagement(
        likes=meta.get("likes_count"),
        views=meta.get("video_view_count") or meta.get("video_play_count"),
        comments_count=meta.get("comments_count"),
    )
