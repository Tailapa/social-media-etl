"""Shared pytest fixtures/factories for unit + integration tests.

Kept intentionally small: most tests build their own lightweight fakes for
the specific repository/client surface they exercise (see
`tests/unit/test_ingestion_pipeline.py` for the reference pattern of an
in-memory fake repository), rather than one large shared mock hierarchy that
would couple every test file's behavior together.
"""

from __future__ import annotations

import os

# Ensure Settings() can construct in CI/dev environments with no .env file
# and no real credentials — every test that needs a *specific* credential
# state should set it explicitly via monkeypatch, not rely on ambient env.
os.environ.setdefault("APP_ENV", "test")

import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

import pytest

from app.models.pydantic import Author, Comment, Engagement, Media, Post
from app.models.pydantic.enums import ContentType, MediaType, PlatformName


@pytest.fixture
def make_author() -> Callable[..., Author]:
    def _make(**overrides: Any) -> Author:
        defaults: dict[str, Any] = {
            "platform": PlatformName.INSTAGRAM,
            "platform_user_id": str(uuid.uuid4()),
            "username": "test_user",
            "follower_count": 100,
        }
        defaults.update(overrides)
        return Author(**defaults)

    return _make


@pytest.fixture
def make_post() -> Callable[..., Post]:
    def _make(*, author_id: str, **overrides: Any) -> Post:
        defaults: dict[str, Any] = {
            "platform": PlatformName.INSTAGRAM,
            "platform_post_id": str(uuid.uuid4()),
            "author_id": author_id,
            "content_type": ContentType.POST,
            "caption": "hello world",
            "posted_at": datetime.now(UTC),
        }
        defaults.update(overrides)
        return Post(**defaults)

    return _make


@pytest.fixture
def make_comment() -> Callable[..., Comment]:
    def _make(*, post_id: str, author_id: str, **overrides: Any) -> Comment:
        defaults: dict[str, Any] = {
            "platform": PlatformName.INSTAGRAM,
            "platform_comment_id": str(uuid.uuid4()),
            "post_id": post_id,
            "author_id": author_id,
            "content": "nice post!",
        }
        defaults.update(overrides)
        return Comment(**defaults)

    return _make


@pytest.fixture
def make_media() -> Callable[..., Media]:
    def _make(**overrides: Any) -> Media:
        defaults: dict[str, Any] = {
            "media_type": MediaType.IMAGE,
            "url": "https://example.com/image.jpg",
        }
        defaults.update(overrides)
        return Media(**defaults)

    return _make


@pytest.fixture
def make_engagement() -> Callable[..., Engagement]:
    def _make(**overrides: Any) -> Engagement:
        defaults: dict[str, Any] = {"likes": 10, "views": 100, "comments_count": 2}
        defaults.update(overrides)
        return Engagement(**defaults)

    return _make
