"""Extended unit tests for the repository layer's platform-specific query
methods (`get_by_*`, `by_*`, `trending`, `link`/`bulk_link`, `mark_*`,
`match`, ...) that `tests/unit/test_repositories.py` does not exercise.

Reuses the exact fake-Supabase-client pattern established there
(`FakeResponse`/`FakeTableBuilder`/`FakeSupabaseClient` + a `fake_client`
fixture that monkeypatches `app.repositories.base.get_supabase_client`)
rather than inventing a new one. Kept in a separate file (rather than
growing `test_repositories.py` further) purely to keep that file focused on
`BaseRepository`'s generic CRUD contract.

Each repository's thin query methods are exercised once with a realistic
case -- these are low-risk wrappers around `list_all`/`_table`, so the goal
is coverage of the wrapper's own logic (query filters built, results
deserialized), not exhaustive branch coverage.
"""

from __future__ import annotations

import uuid

import pytest
from postgrest.exceptions import APIError

from app.models.pydantic import (
    AssistantLog,
    Channel,
    Conversation,
    Hashtag,
    Mention,
    Platform,
    PostHashtag,
    QueryLog,
    Video,
)
from app.models.pydantic.enums import PlatformName, ScrapeJobStatus
from app.repositories.author_repository import AuthorRepository
from app.repositories.base import BaseRepository
from app.repositories.channel_repository import ChannelRepository, VideoRepository
from app.repositories.comment_repository import CommentRepository
from app.repositories.conversation_repository import ConversationRepository
from app.repositories.embedding_repository import (
    Document,
    DocumentRepository,
    EmbeddingRepository,
    EmbeddingRow,
)
from app.repositories.engagement_repository import EngagementRepository
from app.repositories.hashtag_repository import HashtagRepository, PostHashtagRepository
from app.repositories.media_repository import MediaRepository
from app.repositories.mention_repository import MentionRepository
from app.repositories.message_repository import MessageRepository
from app.repositories.platform_repository import PlatformRepository
from app.repositories.post_repository import PostRepository
from app.repositories.query_log_repository import AssistantLogRepository, QueryLogRepository
from app.repositories.scrape_job_repository import ScrapeJob, ScrapeJobRepository
from app.utils.exceptions import RecordNotFoundError, RepositoryError
from tests.unit.test_repositories import FakeResponse, FakeSupabaseClient, FakeTableBuilder


@pytest.fixture
def fake_client(monkeypatch) -> FakeSupabaseClient:
    client = FakeSupabaseClient()
    monkeypatch.setattr("app.repositories.base.get_supabase_client", lambda: client)
    return client


# =============================================================================
# BaseRepository -- generic paths not covered by test_repositories.py
# =============================================================================


def test_base_repository_requires_table_name_and_model():
    class Broken(BaseRepository):
        pass

    with pytest.raises(NotImplementedError):
        Broken()


async def test_require_by_id_found(fake_client, make_author):
    author = make_author()
    table = fake_client.tables["authors"] = FakeTableBuilder("authors")
    table._next_response = FakeResponse([author.model_dump(mode="json")])
    repo = AuthorRepository()
    found = await repo.require_by_id(str(author.id))
    assert found.id == author.id


async def test_require_by_id_not_found_raises(fake_client):
    repo = AuthorRepository()
    with pytest.raises(RecordNotFoundError):
        await repo.require_by_id("00000000-0000-0000-0000-000000000000")


async def test_upsert_maps_api_error_to_repository_error(fake_client, make_author):
    class RaisingTable(FakeTableBuilder):
        def execute(self):
            raise APIError({"message": "boom", "code": "99999", "details": None, "hint": None})

    fake_client.tables["authors"] = RaisingTable("authors")
    repo = AuthorRepository()
    with pytest.raises(RepositoryError):
        await repo.upsert_author(make_author())


async def test_bulk_upsert_maps_api_error_to_repository_error(fake_client, make_author):
    class RaisingTable(FakeTableBuilder):
        def execute(self):
            raise APIError({"message": "boom", "code": "99999", "details": None, "hint": None})

    fake_client.tables["authors"] = RaisingTable("authors")
    repo = AuthorRepository()
    with pytest.raises(RepositoryError):
        await repo.bulk_upsert_authors([make_author()])


async def test_update_maps_api_error_to_repository_error(fake_client):
    class RaisingTable(FakeTableBuilder):
        def execute(self):
            raise APIError({"message": "boom", "code": "99999", "details": None, "hint": None})

    fake_client.tables["authors"] = RaisingTable("authors")
    repo = AuthorRepository()
    with pytest.raises(RepositoryError):
        await repo.update("some-id", {"username": "x"})


async def test_count_with_filters_applies_eq(fake_client):
    table = fake_client.tables["authors"] = FakeTableBuilder("authors")
    table._next_response = FakeResponse([], count=7)
    repo = AuthorRepository()
    result = await repo.count(filters={"platform": "instagram"})
    assert result == 7
    eq_calls = [c for c in table.calls if c[0] == "eq"]
    assert eq_calls[0][1] == ("platform", "instagram")


# =============================================================================
# AuthorRepository
# =============================================================================


async def test_author_get_by_platform_user_id_found(fake_client, make_author):
    author = make_author()
    table = fake_client.tables["authors"] = FakeTableBuilder("authors")
    table._next_response = FakeResponse([author.model_dump(mode="json")])
    repo = AuthorRepository()
    found = await repo.get_by_platform_user_id("instagram", author.platform_user_id)
    assert found is not None
    assert found.id == author.id


async def test_author_get_by_platform_user_id_not_found(fake_client):
    repo = AuthorRepository()
    assert await repo.get_by_platform_user_id("instagram", "nope") is None


async def test_author_most_active_with_and_without_platform(fake_client, make_author):
    author = make_author()
    table = fake_client.tables["authors"] = FakeTableBuilder("authors")
    table._next_response = FakeResponse([author.model_dump(mode="json")])
    repo = AuthorRepository()
    result = await repo.most_active(platform="instagram", limit=5)
    assert len(result) == 1
    result_no_platform = await repo.most_active(limit=5)
    assert len(result_no_platform) == 1


# =============================================================================
# ChannelRepository / VideoRepository
# =============================================================================


def _make_channel(**overrides) -> Channel:
    defaults = {
        "platform": PlatformName.YOUTUBE,
        "platform_channel_id": "chan-1",
        "author_id": "author-1",
        "name": "Test Channel",
    }
    defaults.update(overrides)
    return Channel(**defaults)


def _make_video(**overrides) -> Video:
    defaults = {
        "platform": PlatformName.YOUTUBE,
        "platform_video_id": "vid-1",
        "channel_id": "chan-1",
        "title": "Test Video",
    }
    defaults.update(overrides)
    return Video(**defaults)


async def test_channel_get_by_platform_channel_id_found(fake_client):
    channel = _make_channel()
    table = fake_client.tables["channels"] = FakeTableBuilder("channels")
    table._next_response = FakeResponse([channel.model_dump(mode="json")])
    repo = ChannelRepository()
    found = await repo.get_by_platform_channel_id("youtube", "chan-1")
    assert found is not None
    assert found.id == channel.id


async def test_channel_get_by_platform_channel_id_not_found(fake_client):
    repo = ChannelRepository()
    assert await repo.get_by_platform_channel_id("youtube", "missing") is None


async def test_channel_upsert_and_bulk_upsert_drop_id(fake_client):
    repo = ChannelRepository()
    channel = _make_channel()
    await repo.upsert_channel(channel)
    await repo.bulk_upsert_channels([_make_channel(platform_channel_id="chan-2")])
    table = fake_client.tables["channels"]
    upsert_calls = [c for c in table.calls if c[0] == "upsert"]
    assert len(upsert_calls) == 2
    assert "id" not in upsert_calls[0][1][0]


async def test_channel_by_author(fake_client):
    channel = _make_channel()
    table = fake_client.tables["channels"] = FakeTableBuilder("channels")
    table._next_response = FakeResponse([channel.model_dump(mode="json")])
    repo = ChannelRepository()
    result = await repo.by_author("author-1")
    assert len(result) == 1
    eq_calls = [c for c in table.calls if c[0] == "eq"]
    assert ("author_id", "author-1") in [c[1] for c in eq_calls]


async def test_video_get_by_platform_video_id_found(fake_client):
    video = _make_video()
    table = fake_client.tables["videos"] = FakeTableBuilder("videos")
    table._next_response = FakeResponse([video.model_dump(mode="json")])
    repo = VideoRepository()
    found = await repo.get_by_platform_video_id("youtube", "vid-1")
    assert found is not None
    assert found.id == video.id


async def test_video_get_by_platform_video_id_not_found(fake_client):
    repo = VideoRepository()
    assert await repo.get_by_platform_video_id("youtube", "missing") is None


async def test_video_upsert_and_bulk_upsert_drop_id(fake_client):
    repo = VideoRepository()
    await repo.upsert_video(_make_video())
    await repo.bulk_upsert_videos([_make_video(platform_video_id="vid-2")])
    table = fake_client.tables["videos"]
    upsert_calls = [c for c in table.calls if c[0] == "upsert"]
    assert len(upsert_calls) == 2


async def test_video_by_channel(fake_client):
    video = _make_video()
    table = fake_client.tables["videos"] = FakeTableBuilder("videos")
    table._next_response = FakeResponse([video.model_dump(mode="json")])
    repo = VideoRepository()
    result = await repo.by_channel("chan-1", limit=10)
    assert len(result) == 1


# =============================================================================
# HashtagRepository / PostHashtagRepository
# =============================================================================


async def test_hashtag_get_by_tag_found(fake_client):
    hashtag = Hashtag(tag="sunset")
    table = fake_client.tables["hashtags"] = FakeTableBuilder("hashtags")
    table._next_response = FakeResponse([hashtag.model_dump(mode="json")])
    repo = HashtagRepository()
    found = await repo.get_by_tag("#Sunset")
    assert found is not None
    assert found.tag == "sunset"


async def test_hashtag_get_by_tag_not_found(fake_client):
    repo = HashtagRepository()
    assert await repo.get_by_tag("missing") is None


async def test_hashtag_upsert_and_bulk_upsert(fake_client):
    repo = HashtagRepository()
    await repo.upsert_tag(Hashtag(tag="one"))
    await repo.bulk_upsert_tags([Hashtag(tag="two"), Hashtag(tag="three")])
    table = fake_client.tables["hashtags"]
    upsert_calls = [c for c in table.calls if c[0] == "upsert"]
    assert len(upsert_calls) == 2


async def test_hashtag_trending_returns_raw_rows(fake_client):
    table = fake_client.tables["hashtags"] = FakeTableBuilder("hashtags")
    table._next_response = FakeResponse([{"id": "1", "tag": "sunset"}])
    repo = HashtagRepository()
    rows = await repo.trending(limit=5)
    assert rows == [{"id": "1", "tag": "sunset"}]
    order_calls = [c for c in table.calls if c[0] == "order"]
    assert order_calls[0][1] == ("created_at",)
    assert order_calls[0][2] == {"desc": True}


async def test_post_hashtag_link_upserts_row(fake_client):
    repo = PostHashtagRepository()
    await repo.link("post-1", "hashtag-1")
    table = fake_client.tables["post_hashtags"]
    upsert_calls = [c for c in table.calls if c[0] == "upsert"]
    assert len(upsert_calls) == 1
    payload, kwargs = upsert_calls[0][1][0], upsert_calls[0][2]
    assert payload == {"post_id": "post-1", "hashtag_id": "hashtag-1"}
    assert kwargs["on_conflict"] == "post_id,hashtag_id"


async def test_post_hashtag_bulk_link_upserts_all(fake_client):
    repo = PostHashtagRepository()
    links = [
        PostHashtag(post_id="p1", hashtag_id="h1"),
        PostHashtag(post_id="p2", hashtag_id="h2"),
    ]
    await repo.bulk_link(links)
    table = fake_client.tables["post_hashtags"]
    upsert_calls = [c for c in table.calls if c[0] == "upsert"]
    assert len(upsert_calls) == 1
    assert len(upsert_calls[0][1][0]) == 2


async def test_post_hashtag_bulk_link_empty_list_short_circuits(fake_client):
    repo = PostHashtagRepository()
    await repo.bulk_link([])
    assert "post_hashtags" not in fake_client.tables


async def test_post_hashtag_hashtags_for_post(fake_client):
    table = fake_client.tables["post_hashtags"] = FakeTableBuilder("post_hashtags")
    table._next_response = FakeResponse([{"post_id": "p1", "hashtag_id": "h1"}])
    repo = PostHashtagRepository()
    result = await repo.hashtags_for_post("p1")
    assert len(result) == 1
    assert result[0].hashtag_id == "h1"


# =============================================================================
# CommentRepository
# =============================================================================


async def test_comment_get_by_platform_comment_id_found(fake_client, make_comment):
    comment = make_comment(post_id="post-1", author_id="author-1")
    table = fake_client.tables["comments"] = FakeTableBuilder("comments")
    table._next_response = FakeResponse([comment.model_dump(mode="json")])
    repo = CommentRepository()
    found = await repo.get_by_platform_comment_id("instagram", comment.platform_comment_id)
    assert found is not None
    assert found.id == comment.id


async def test_comment_get_by_platform_comment_id_not_found(fake_client):
    repo = CommentRepository()
    assert await repo.get_by_platform_comment_id("instagram", "missing") is None


async def test_comment_upsert_and_bulk_upsert(fake_client, make_comment):
    repo = CommentRepository()
    await repo.upsert_comment(make_comment(post_id="post-1", author_id="author-1"))
    await repo.bulk_upsert_comments(
        [make_comment(post_id="post-1", author_id="author-1", content="another")]
    )
    table = fake_client.tables["comments"]
    upsert_calls = [c for c in table.calls if c[0] == "upsert"]
    assert len(upsert_calls) == 2


async def test_comment_by_post(fake_client, make_comment):
    comment = make_comment(post_id="post-1", author_id="author-1")
    table = fake_client.tables["comments"] = FakeTableBuilder("comments")
    table._next_response = FakeResponse([comment.model_dump(mode="json")])
    repo = CommentRepository()
    result = await repo.by_post("post-1")
    assert len(result) == 1


async def test_comment_replies_to(fake_client, make_comment):
    reply = make_comment(post_id="post-1", author_id="author-2", parent_comment_id="parent-1")
    table = fake_client.tables["comments"] = FakeTableBuilder("comments")
    table._next_response = FakeResponse([reply.model_dump(mode="json")])
    repo = CommentRepository()
    result = await repo.replies_to("parent-1")
    assert len(result) == 1
    assert result[0].parent_comment_id == "parent-1"


# =============================================================================
# MentionRepository
# =============================================================================


async def test_mention_by_post(fake_client):
    mention = Mention(post_id="post-1", username="alice")
    table = fake_client.tables["mentions"] = FakeTableBuilder("mentions")
    table._next_response = FakeResponse([mention.model_dump(mode="json")])
    repo = MentionRepository()
    result = await repo.by_post("post-1")
    assert len(result) == 1


async def test_mention_by_comment(fake_client):
    mention = Mention(comment_id="comment-1", username="bob")
    table = fake_client.tables["mentions"] = FakeTableBuilder("mentions")
    table._next_response = FakeResponse([mention.model_dump(mode="json")])
    repo = MentionRepository()
    result = await repo.by_comment("comment-1")
    assert len(result) == 1


async def test_mention_by_username_normalizes(fake_client):
    mention = Mention(username="carol")
    table = fake_client.tables["mentions"] = FakeTableBuilder("mentions")
    table._next_response = FakeResponse([mention.model_dump(mode="json")])
    repo = MentionRepository()
    result = await repo.by_username("@Carol")
    assert len(result) == 1
    eq_calls = [c for c in table.calls if c[0] == "eq"]
    assert ("username", "carol") in [c[1] for c in eq_calls]


async def test_mention_bulk_create_inserts_all(fake_client):
    mentions = [Mention(username="alice"), Mention(username="bob")]
    fake_client.tables["mentions"] = FakeTableBuilder(
        "mentions",
        responses={"insert": FakeResponse([m.model_dump(mode="json") for m in mentions])},
    )
    repo = MentionRepository()
    result = await repo.bulk_create_mentions(mentions)
    assert len(result) == 2


async def test_mention_bulk_create_empty_list_short_circuits(fake_client):
    repo = MentionRepository()
    assert await repo.bulk_create_mentions([]) == []
    assert "mentions" not in fake_client.tables


# =============================================================================
# PostRepository
# =============================================================================


async def test_post_get_by_platform_post_id_found(fake_client, make_post):
    post = make_post(author_id="author-1")
    table = fake_client.tables["posts"] = FakeTableBuilder("posts")
    table._next_response = FakeResponse([post.model_dump(mode="json")])
    repo = PostRepository()
    found = await repo.get_by_platform_post_id("instagram", post.platform_post_id)
    assert found is not None
    assert found.id == post.id


async def test_post_get_by_platform_post_id_not_found(fake_client):
    repo = PostRepository()
    assert await repo.get_by_platform_post_id("instagram", "missing") is None


async def test_post_by_platform(fake_client, make_post):
    post = make_post(author_id="author-1")
    table = fake_client.tables["posts"] = FakeTableBuilder("posts")
    table._next_response = FakeResponse([post.model_dump(mode="json")])
    repo = PostRepository()
    result = await repo.by_platform("instagram", limit=10, offset=0)
    assert len(result) == 1


async def test_post_by_author(fake_client, make_post):
    post = make_post(author_id="author-1")
    table = fake_client.tables["posts"] = FakeTableBuilder("posts")
    table._next_response = FakeResponse([post.model_dump(mode="json")])
    repo = PostRepository()
    result = await repo.by_author("author-1")
    assert len(result) == 1


async def test_post_posted_between_with_platform_filter(fake_client, make_post):
    from datetime import UTC, datetime

    post = make_post(author_id="author-1")
    table = fake_client.tables["posts"] = FakeTableBuilder("posts")
    table._next_response = FakeResponse([post.model_dump(mode="json")])
    repo = PostRepository()
    result = await repo.posted_between(
        datetime(2024, 1, 1, tzinfo=UTC), datetime(2024, 12, 31, tzinfo=UTC), platform="instagram"
    )
    assert len(result) == 1
    eq_calls = [c for c in table.calls if c[0] == "eq"]
    assert ("platform", "instagram") in [c[1] for c in eq_calls]


# =============================================================================
# MediaRepository
# =============================================================================


async def test_media_by_post(fake_client, make_media):
    media = make_media(post_id="post-1")
    table = fake_client.tables["media"] = FakeTableBuilder("media")
    table._next_response = FakeResponse([media.model_dump(mode="json")])
    repo = MediaRepository()
    result = await repo.by_post("post-1")
    assert len(result) == 1


async def test_media_bulk_create_inserts_all(fake_client, make_media):
    items = [
        make_media(url="https://example.com/a.jpg"),
        make_media(url="https://example.com/b.jpg"),
    ]
    fake_client.tables["media"] = FakeTableBuilder(
        "media", responses={"insert": FakeResponse([m.model_dump(mode="json") for m in items])}
    )
    repo = MediaRepository()
    result = await repo.bulk_create_media(items)
    assert len(result) == 2


async def test_media_bulk_create_empty_list_short_circuits(fake_client):
    repo = MediaRepository()
    assert await repo.bulk_create_media([]) == []
    assert "media" not in fake_client.tables


# =============================================================================
# ConversationRepository
# =============================================================================


async def test_conversation_by_user(fake_client):
    conversation = Conversation(user_id="user-1", title="hi")
    table = fake_client.tables["conversations"] = FakeTableBuilder("conversations")
    table._next_response = FakeResponse([conversation.model_dump(mode="json")])
    repo = ConversationRepository()
    result = await repo.by_user("user-1")
    assert len(result) == 1


async def test_conversation_search_by_title(fake_client):
    conversation = Conversation(title="hello world")
    table = fake_client.tables["conversations"] = FakeTableBuilder("conversations")
    table._next_response = FakeResponse([conversation.model_dump(mode="json")])
    repo = ConversationRepository()
    result = await repo.search_by_title("hello")
    assert len(result) == 1
    ilike_calls = [c for c in table.calls if c[0] == "ilike"]
    assert ilike_calls[0][1] == ("title", "%hello%")


async def test_conversation_archive(fake_client):
    conversation = Conversation(title="archived one", is_archived=True)
    fake_client.tables["conversations"] = FakeTableBuilder(
        "conversations", responses={"update": FakeResponse([conversation.model_dump(mode="json")])}
    )
    repo = ConversationRepository()
    result = await repo.archive(str(conversation.id))
    assert result.is_archived is True
    table = fake_client.tables["conversations"]
    update_calls = [c for c in table.calls if c[0] == "update"]
    assert update_calls[0][1][0] == {"is_archived": True}


# =============================================================================
# QueryLogRepository / AssistantLogRepository
# =============================================================================


async def test_query_log_by_conversation(fake_client):
    log = QueryLog(conversation_id="conv-1", query_text="how many posts?")
    table = fake_client.tables["query_logs"] = FakeTableBuilder("query_logs")
    table._next_response = FakeResponse([log.model_dump(mode="json")])
    repo = QueryLogRepository()
    result = await repo.by_conversation("conv-1")
    assert len(result) == 1


async def test_query_log_recent(fake_client):
    log = QueryLog(query_text="recent question")
    table = fake_client.tables["query_logs"] = FakeTableBuilder("query_logs")
    table._next_response = FakeResponse([log.model_dump(mode="json")])
    repo = QueryLogRepository()
    result = await repo.recent(limit=5)
    assert len(result) == 1


async def test_assistant_log_by_conversation(fake_client):
    log = AssistantLog(conversation_id="conv-1", prompt_used="p", model_used="gpt-4")
    table = fake_client.tables["assistant_logs"] = FakeTableBuilder("assistant_logs")
    table._next_response = FakeResponse([log.model_dump(mode="json")])
    repo = AssistantLogRepository()
    result = await repo.by_conversation("conv-1")
    assert len(result) == 1


async def test_assistant_log_failures_filters_only_errored_logs(fake_client):
    ok_log = AssistantLog(prompt_used="q1", model_used="gpt-4")
    bad_log = AssistantLog(prompt_used="q2", model_used="gpt-4", error="boom")
    table = fake_client.tables["assistant_logs"] = FakeTableBuilder("assistant_logs")
    table._next_response = FakeResponse(
        [ok_log.model_dump(mode="json"), bad_log.model_dump(mode="json")]
    )
    repo = AssistantLogRepository()
    failures = await repo.failures(limit=10)
    assert len(failures) == 1
    assert failures[0].error == "boom"


# =============================================================================
# ScrapeJobRepository
# =============================================================================


async def test_scrape_job_start_creates_running_job(fake_client):
    repo = ScrapeJobRepository()
    job = await repo.start("instagram", "posts", target="someuser")
    table = fake_client.tables["scrape_jobs"]
    insert_calls = [c for c in table.calls if c[0] == "insert"]
    assert len(insert_calls) == 1
    payload = insert_calls[0][1][0]
    assert payload["status"] == ScrapeJobStatus.RUNNING.value
    assert payload["target"] == "someuser"
    assert job.status == ScrapeJobStatus.RUNNING.value


async def test_scrape_job_mark_succeeded(fake_client):
    job = ScrapeJob(platform=PlatformName.INSTAGRAM, job_type="posts")
    fake_client.tables["scrape_jobs"] = FakeTableBuilder(
        "scrape_jobs", responses={"update": FakeResponse([job.model_dump(mode="json")])}
    )
    repo = ScrapeJobRepository()
    result = await repo.mark_succeeded(str(job.id), 42)
    table = fake_client.tables["scrape_jobs"]
    data = [c for c in table.calls if c[0] == "update"][0][1][0]
    assert data["status"] == ScrapeJobStatus.SUCCEEDED.value
    assert data["records_scraped"] == 42
    assert result.id == job.id


async def test_scrape_job_mark_partial(fake_client):
    job = ScrapeJob(platform=PlatformName.INSTAGRAM, job_type="posts")
    fake_client.tables["scrape_jobs"] = FakeTableBuilder(
        "scrape_jobs", responses={"update": FakeResponse([job.model_dump(mode="json")])}
    )
    repo = ScrapeJobRepository()
    await repo.mark_partial(str(job.id), 10, "partial failure")
    table = fake_client.tables["scrape_jobs"]
    data = [c for c in table.calls if c[0] == "update"][0][1][0]
    assert data["status"] == ScrapeJobStatus.PARTIAL.value
    assert data["error"] == "partial failure"


async def test_scrape_job_mark_failed(fake_client):
    job = ScrapeJob(platform=PlatformName.INSTAGRAM, job_type="posts")
    fake_client.tables["scrape_jobs"] = FakeTableBuilder(
        "scrape_jobs", responses={"update": FakeResponse([job.model_dump(mode="json")])}
    )
    repo = ScrapeJobRepository()
    await repo.mark_failed(str(job.id), "total failure")
    table = fake_client.tables["scrape_jobs"]
    data = [c for c in table.calls if c[0] == "update"][0][1][0]
    assert data["status"] == ScrapeJobStatus.FAILED.value
    assert data["error"] == "total failure"


async def test_scrape_job_recent_with_and_without_platform(fake_client):
    job = ScrapeJob(platform=PlatformName.INSTAGRAM, job_type="posts")
    table = fake_client.tables["scrape_jobs"] = FakeTableBuilder("scrape_jobs")
    table._next_response = FakeResponse([job.model_dump(mode="json")])
    repo = ScrapeJobRepository()
    result = await repo.recent(platform="instagram", limit=5)
    assert len(result) == 1
    result_no_platform = await repo.recent(limit=5)
    assert len(result_no_platform) == 1


# =============================================================================
# EmbeddingRepository / DocumentRepository
# =============================================================================


def _make_document(**overrides) -> Document:
    defaults = {
        "source_type": "post",
        "source_id": "post-1",
        "platform": PlatformName.INSTAGRAM,
        "content": "hello",
    }
    defaults.update(overrides)
    return Document(**defaults)


def _make_embedding_row(**overrides) -> EmbeddingRow:
    defaults = {
        "document_id": str(uuid.uuid4()),
        "source_type": "post",
        "source_id": "post-1",
        "platform": PlatformName.INSTAGRAM,
        "model": "text-embedding-3-small",
        "dimensions": 3,
        "checksum": "abc123",
        "vector": [0.1, 0.2, 0.3],
    }
    defaults.update(overrides)
    return EmbeddingRow(**defaults)


async def test_document_get_by_source_found(fake_client):
    document = _make_document()
    table = fake_client.tables["documents"] = FakeTableBuilder("documents")
    table._next_response = FakeResponse([document.model_dump(mode="json")])
    repo = DocumentRepository()
    found = await repo.get_by_source("post", "post-1")
    assert found is not None
    assert found.id == document.id


async def test_document_get_by_source_not_found(fake_client):
    repo = DocumentRepository()
    assert await repo.get_by_source("post", "missing") is None


async def test_document_upsert_document(fake_client):
    repo = DocumentRepository()
    await repo.upsert_document(_make_document())
    table = fake_client.tables["documents"]
    upsert_calls = [c for c in table.calls if c[0] == "upsert"]
    assert len(upsert_calls) == 1
    assert upsert_calls[0][2]["on_conflict"] == "source_type,source_id"


async def test_embedding_get_by_checksum_found(fake_client):
    row = _make_embedding_row()
    table = fake_client.tables["embeddings"] = FakeTableBuilder("embeddings")
    table._next_response = FakeResponse([row.model_dump(mode="json")])
    repo = EmbeddingRepository()
    found = await repo.get_by_checksum("post-1", "post", "text-embedding-3-small")
    assert found is not None
    assert found.checksum == "abc123"


async def test_embedding_get_by_checksum_not_found(fake_client):
    repo = EmbeddingRepository()
    assert await repo.get_by_checksum("post-1", "post", "text-embedding-3-small") is None


async def test_embedding_upsert_and_bulk_upsert(fake_client):
    repo = EmbeddingRepository()
    await repo.upsert_embedding(_make_embedding_row())
    await repo.bulk_upsert_embeddings([_make_embedding_row(source_id="post-2")])
    table = fake_client.tables["embeddings"]
    upsert_calls = [c for c in table.calls if c[0] == "upsert"]
    assert len(upsert_calls) == 2
    assert upsert_calls[0][2]["on_conflict"] == "source_id,source_type,model"


class _FakeRpcBuilder:
    def __init__(self, response: FakeResponse) -> None:
        self._response = response

    def execute(self) -> FakeResponse:
        return self._response


class FakeSupabaseClientWithRpc(FakeSupabaseClient):
    def __init__(self) -> None:
        super().__init__()
        self.rpc_calls: list[tuple[str, dict]] = []
        self.rpc_response = FakeResponse([])

    def rpc(self, fn_name: str, params: dict) -> _FakeRpcBuilder:
        self.rpc_calls.append((fn_name, params))
        return _FakeRpcBuilder(self.rpc_response)


async def test_embedding_match_calls_match_embeddings_rpc(monkeypatch):
    client = FakeSupabaseClientWithRpc()
    client.rpc_response = FakeResponse([{"source_id": "post-1", "similarity": 0.9}])
    monkeypatch.setattr("app.repositories.embedding_repository.get_supabase_client", lambda: client)
    repo = EmbeddingRepository()
    results = await repo.match([0.1, 0.2, 0.3], match_count=5, platform="instagram")
    assert results == [{"source_id": "post-1", "similarity": 0.9}]
    fn_name, params = client.rpc_calls[0]
    assert fn_name == "match_embeddings"
    assert params["match_count"] == 5
    assert params["filter_platform"] == "instagram"


# =============================================================================
# PlatformRepository
# =============================================================================


async def test_platform_get_by_name_found(fake_client):
    platform = Platform(name=PlatformName.INSTAGRAM, display_name="Instagram")
    table = fake_client.tables["platforms"] = FakeTableBuilder("platforms")
    table._next_response = FakeResponse([platform.model_dump(mode="json")])
    repo = PlatformRepository()
    found = await repo.get_by_name("instagram")
    assert found is not None
    assert found.display_name == "Instagram"


async def test_platform_get_by_name_not_found(fake_client):
    repo = PlatformRepository()
    assert await repo.get_by_name("missing") is None


# =============================================================================
# EngagementRepository
# =============================================================================


async def test_engagement_get_by_post_found(fake_client, make_engagement):
    engagement = make_engagement(post_id="post-1")
    table = fake_client.tables["engagement"] = FakeTableBuilder("engagement")
    table._next_response = FakeResponse([engagement.model_dump(mode="json")])
    repo = EngagementRepository()
    found = await repo.get_by_post("post-1")
    assert found is not None
    assert found.likes == 10


async def test_engagement_get_by_post_not_found(fake_client):
    repo = EngagementRepository()
    assert await repo.get_by_post("missing") is None


async def test_engagement_upsert_for_post(fake_client, make_engagement):
    repo = EngagementRepository()
    await repo.upsert_for_post(make_engagement(post_id="post-1"))
    table = fake_client.tables["engagement"]
    upsert_calls = [c for c in table.calls if c[0] == "upsert"]
    assert upsert_calls[0][2]["on_conflict"] == "post_id"


async def test_engagement_top_by_likes(fake_client, make_engagement):
    engagement = make_engagement(post_id="post-1", likes=999)
    table = fake_client.tables["engagement"] = FakeTableBuilder("engagement")
    table._next_response = FakeResponse([engagement.model_dump(mode="json")])
    repo = EngagementRepository()
    result = await repo.top_by_likes(limit=10)
    assert len(result) == 1
    assert result[0].likes == 999


# =============================================================================
# MessageRepository
# =============================================================================


async def test_message_by_conversation(fake_client):
    from app.models.pydantic import ChatMessage
    from app.models.pydantic.enums import MessageRole

    message = ChatMessage(conversation_id="conv-1", role=MessageRole.USER, content="hi")
    table = fake_client.tables["messages"] = FakeTableBuilder("messages")
    table._next_response = FakeResponse([message.model_dump(mode="json")])
    repo = MessageRepository()
    result = await repo.by_conversation("conv-1")
    assert len(result) == 1
