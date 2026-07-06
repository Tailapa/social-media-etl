"""Integration tests for app/services/{chat,scrape,analytics}_service.py.

Every one of these services accepts its dependencies as optional
constructor kwargs, so every test here injects small in-memory fakes rather
than touching Supabase, Apify, or OpenAI.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

from app.apify.base.scraper import ScrapeResult
from app.ingestion.pipeline import IngestionReport
from app.models.pydantic import ChatMessage, Conversation
from app.models.pydantic.enums import MessageRole, PlatformName
from app.services.analytics_service import AnalyticsService
from app.services.chat_service import ChatService
from app.services.scrape_service import ScrapeService

# =============================================================================
# ScrapeService
# =============================================================================


class FakeScraper:
    def __init__(self) -> None:
        self.calls: list[tuple] = []

    async def scrape_profile(self, identifier: str) -> ScrapeResult:
        self.calls.append(("scrape_profile", identifier))
        return ScrapeResult()

    async def scrape_posts(self, identifier: str, *, limit: int = 50) -> ScrapeResult:
        self.calls.append(("scrape_posts", identifier, limit))
        return ScrapeResult()

    async def scrape_comments(self, post_url_or_id: str, *, limit: int = 100) -> ScrapeResult:
        self.calls.append(("scrape_comments", post_url_or_id, limit))
        return ScrapeResult()

    async def scrape_hashtag(self, hashtag: str, *, limit: int = 50) -> ScrapeResult:
        self.calls.append(("scrape_hashtag", hashtag, limit))
        return ScrapeResult()

    async def scrape_keyword(self, keyword: str, *, limit: int = 50) -> ScrapeResult:
        self.calls.append(("scrape_keyword", keyword, limit))
        return ScrapeResult()


def _make_scrape_service(monkeypatch, fake_scraper: FakeScraper) -> tuple[ScrapeService, Any]:
    monkeypatch.setattr("app.services.scrape_service.get_scraper", lambda platform: fake_scraper)
    fake_pipeline = SimpleNamespace(
        ingest=AsyncMock(return_value=IngestionReport(job_id="job-1", posts_upserted=1))
    )
    service = ScrapeService(pipeline=fake_pipeline)
    return service, fake_pipeline


async def test_scrape_posts_calls_scraper_then_pipeline_with_expected_kwargs(monkeypatch):
    fake_scraper = FakeScraper()
    service, fake_pipeline = _make_scrape_service(monkeypatch, fake_scraper)

    report = await service.scrape_posts("instagram", "someuser", limit=10)

    assert fake_scraper.calls == [("scrape_posts", "someuser", 10)]
    fake_pipeline.ingest.assert_awaited_once()
    _args, kwargs = fake_pipeline.ingest.await_args
    assert kwargs["platform"] == "instagram"
    assert kwargs["job_type"] == "posts"
    assert kwargs["target"] == "someuser"
    assert report.job_id == "job-1"


async def test_scrape_profile_calls_scraper_then_pipeline(monkeypatch):
    fake_scraper = FakeScraper()
    service, fake_pipeline = _make_scrape_service(monkeypatch, fake_scraper)

    await service.scrape_profile("instagram", "someuser")

    assert fake_scraper.calls == [("scrape_profile", "someuser")]
    _args, kwargs = fake_pipeline.ingest.await_args
    assert kwargs["job_type"] == "profile"
    assert kwargs["target"] == "someuser"


async def test_scrape_comments_calls_scraper_then_pipeline(monkeypatch):
    fake_scraper = FakeScraper()
    service, fake_pipeline = _make_scrape_service(monkeypatch, fake_scraper)

    await service.scrape_comments("instagram", "post-url", limit=25)

    assert fake_scraper.calls == [("scrape_comments", "post-url", 25)]
    _args, kwargs = fake_pipeline.ingest.await_args
    assert kwargs["job_type"] == "comments"
    assert kwargs["target"] == "post-url"


async def test_scrape_hashtag_calls_scraper_then_pipeline(monkeypatch):
    fake_scraper = FakeScraper()
    service, fake_pipeline = _make_scrape_service(monkeypatch, fake_scraper)

    await service.scrape_hashtag("instagram", "cats")

    assert fake_scraper.calls == [("scrape_hashtag", "cats", 50)]
    _args, kwargs = fake_pipeline.ingest.await_args
    assert kwargs["job_type"] == "hashtag"
    assert kwargs["target"] == "cats"


async def test_scrape_keyword_calls_scraper_then_pipeline(monkeypatch):
    fake_scraper = FakeScraper()
    service, fake_pipeline = _make_scrape_service(monkeypatch, fake_scraper)

    await service.scrape_keyword("instagram", "election")

    assert fake_scraper.calls == [("scrape_keyword", "election", 50)]
    _args, kwargs = fake_pipeline.ingest.await_args
    assert kwargs["job_type"] == "keyword"
    assert kwargs["target"] == "election"


async def test_scrape_many_runs_all_tasks_concurrently_bounded_by_max_concurrency(monkeypatch):
    from app.services.scrape_service import ScrapeTask

    fake_scraper = FakeScraper()
    monkeypatch.setattr("app.services.scrape_service.get_scraper", lambda platform: fake_scraper)
    fake_pipeline = SimpleNamespace(
        ingest=AsyncMock(return_value=IngestionReport(job_id="job-1", posts_upserted=1))
    )
    service = ScrapeService(pipeline=fake_pipeline, max_concurrency=2)

    tasks = [
        ScrapeTask(platform="instagram", mode="posts", target="nasa", limit=10),
        ScrapeTask(platform="instagram", mode="hashtag", target="space", limit=20),
        ScrapeTask(platform="instagram", mode="profile", target="esa"),
    ]
    reports = await service.scrape_many(tasks)

    assert len(reports) == 3
    assert all(r.job_id == "job-1" for r in reports)
    assert ("scrape_posts", "nasa", 10) in fake_scraper.calls
    assert ("scrape_hashtag", "space", 20) in fake_scraper.calls
    assert ("scrape_profile", "esa") in fake_scraper.calls
    assert fake_pipeline.ingest.await_count == 3


# =============================================================================
# AnalyticsService
# =============================================================================


class FakeCountRepo:
    def __init__(self, count: int) -> None:
        self._count = count
        self.calls: list[dict | None] = []

    async def count(self, filters: dict | None = None) -> int:
        self.calls.append(filters)
        return self._count


class FakeAuthorRepo:
    async def most_active(self, *, limit: int = 10) -> list:
        return []


class FakeHashtagRepo:
    async def trending(self, *, limit: int = 10) -> list:
        return []


class FakeEngagementRepo:
    async def top_by_likes(self, *, limit: int = 10) -> list:
        return []


class FakeScrapeJobRepo:
    async def recent(self, *, limit: int = 20) -> list:
        return []


class FakeQueryLogRepo:
    def __init__(self, logs: list | None = None) -> None:
        self.logs = logs or []

    async def recent(self, *, limit: int = 200) -> list:
        return self.logs


def _make_analytics_service(
    *, post_count: int = 5, comment_count: int = 3, query_logs: list | None = None
) -> AnalyticsService:
    return AnalyticsService(
        post_repo=FakeCountRepo(post_count),
        comment_repo=FakeCountRepo(comment_count),
        author_repo=FakeAuthorRepo(),
        engagement_repo=FakeEngagementRepo(),
        hashtag_repo=FakeHashtagRepo(),
        scrape_job_repo=FakeScrapeJobRepo(),
        query_log_repo=FakeQueryLogRepo(query_logs),
    )


async def test_dashboard_summary_has_all_expected_keys():
    service = _make_analytics_service()

    summary = await service.dashboard_summary()

    assert set(summary.keys()) == {
        "total_posts",
        "total_comments",
        "platform_distribution",
        "most_active_authors",
        "trending_hashtags",
        "top_engagement_posts",
        "recent_scrape_jobs",
        "ai_query_stats",
    }
    assert summary["total_posts"] == 5
    assert summary["total_comments"] == 3
    assert summary["platform_distribution"] == {p.value: 5 for p in PlatformName}
    assert summary["most_active_authors"] == []
    assert summary["ai_query_stats"] == {"total_queries": 0, "avg_latency_ms": None}


async def test_ai_query_stats_computes_average_latency():
    logs = [SimpleNamespace(latency_ms=100.0), SimpleNamespace(latency_ms=200.0)]
    service = _make_analytics_service(query_logs=logs)

    stats = await service.ai_query_stats()

    assert stats == {"total_queries": 2, "avg_latency_ms": 150.0}


# =============================================================================
# ChatService
# =============================================================================


class FakeConversationRepoForChat:
    def __init__(self, conversation: Conversation) -> None:
        self.conversation = conversation

    async def require_by_id(self, conversation_id: str) -> Conversation:
        return self.conversation


class FakeMessageRepoForChat:
    def __init__(self, messages: list[ChatMessage]) -> None:
        self.messages = messages

    async def by_conversation(self, conversation_id: str, **kwargs: Any) -> list[ChatMessage]:
        return self.messages


async def test_export_conversation_produces_markdown_with_title_and_messages():
    conversation = Conversation(title="My Convo")
    messages = [
        ChatMessage(
            conversation_id=str(conversation.id), role=MessageRole.USER, content="Hello there"
        ),
        ChatMessage(
            conversation_id=str(conversation.id),
            role=MessageRole.ASSISTANT,
            content="Hi, how can I help?",
            sources=["post:abc123"],
        ),
    ]
    service = ChatService(
        assistant=SimpleNamespace(),
        conversation_repo=FakeConversationRepoForChat(conversation),
        message_repo=FakeMessageRepoForChat(messages),
    )

    markdown = await service.export_conversation(str(conversation.id))

    assert "# My Convo" in markdown
    assert "Hello there" in markdown
    assert "Hi, how can I help?" in markdown
    assert "post:abc123" in markdown


async def test_ask_delegates_to_assistant():
    fake_message = ChatMessage(conversation_id="conv-1", role=MessageRole.ASSISTANT, content="hi")
    fake_assistant = SimpleNamespace(ask=AsyncMock(return_value=fake_message))
    service = ChatService(
        assistant=fake_assistant,
        conversation_repo=SimpleNamespace(),
        message_repo=SimpleNamespace(),
    )

    result = await service.ask("What's up?", conversation_id="conv-1")

    fake_assistant.ask.assert_awaited_once_with("What's up?", conversation_id="conv-1")
    assert result is fake_message
