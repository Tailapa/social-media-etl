"""Shared enums used across every Pydantic model and platform scraper."""

from __future__ import annotations

from enum import StrEnum


class PlatformName(StrEnum):
    """Supported (and future) platform identifiers.

    Adding a platform here plus a matching scraper + normalizer is the only
    change needed to onboard a new source; no existing model changes shape.
    """

    INSTAGRAM = "instagram"
    TWITTER = "twitter"
    YOUTUBE = "youtube"
    # Reserved for future extensibility (see docs/architecture.md).
    REDDIT = "reddit"
    LINKEDIN = "linkedin"
    FACEBOOK = "facebook"
    TIKTOK = "tiktok"
    NEWS = "news"


class MediaType(StrEnum):
    IMAGE = "image"
    VIDEO = "video"
    AUDIO = "audio"
    CAROUSEL = "carousel"
    GIF = "gif"
    OTHER = "other"


class ContentType(StrEnum):
    """The kind of top-level content a Post represents."""

    POST = "post"
    REEL = "reel"
    STORY = "story"
    TWEET = "tweet"
    RETWEET = "retweet"
    QUOTE = "quote"
    VIDEO = "video"
    SHORT = "short"
    LIVE = "live"


class MessageRole(StrEnum):
    USER = "user"
    ASSISTANT = "assistant"
    SYSTEM = "system"


class EmbeddingSourceType(StrEnum):
    POST = "post"
    COMMENT = "comment"
    CAPTION = "caption"
    DESCRIPTION = "description"
    TRANSCRIPT = "transcript"


class ScrapeJobStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    PARTIAL = "partial"
