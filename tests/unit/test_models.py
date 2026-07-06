"""Unit tests for the Pydantic domain models in app/models/pydantic/."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from app.models.pydantic import (
    Author,
    Channel,
    Comment,
    Conversation,
    Engagement,
    Hashtag,
    Mention,
    Post,
    Reply,
    Thread,
    Video,
)
from app.models.pydantic.embedding import EmbeddingDocument
from app.models.pydantic.enums import (
    ContentType,
    EmbeddingSourceType,
    MediaType,
    PlatformName,
)

# --- Author -----------------------------------------------------------------


def test_author_username_strips_at_symbol(make_author):
    author = make_author(username="@some_user")
    assert author.username == "some_user"


def test_author_username_strips_whitespace_and_at(make_author):
    author = make_author(username="  @some_user  ")
    assert author.username == "some_user"


def test_author_dedup_key(make_author):
    author = make_author(platform=PlatformName.TWITTER, platform_user_id="123")
    assert author.dedup_key == "twitter:123"


def test_author_round_trip(make_author):
    author = make_author(display_name="Some User", bio="hello")
    restored = Author.model_validate(author.model_dump())
    assert restored == author


# --- Post ---------------------------------------------------------------------


def test_post_hashtags_normalized_lowercase_no_hash(make_post):
    post = make_post(author_id="a1", hashtags=["#Foo", "BAR", "#baz"])
    assert post.hashtags == ["foo", "bar", "baz"]


def test_post_mentions_normalized_lowercase_no_at(make_post):
    post = make_post(author_id="a1", mentions=["@Alice", "bob"])
    assert post.mentions == ["alice", "bob"]


def test_post_hashtags_defaults_to_empty_list(make_post):
    post = make_post(author_id="a1", hashtags=None)
    assert post.hashtags == []


def test_post_dedup_key(make_post):
    post = make_post(author_id="a1", platform=PlatformName.YOUTUBE, platform_post_id="vid1")
    assert post.dedup_key == "youtube:vid1"


def test_post_has_media_false_by_default(make_post):
    post = make_post(author_id="a1")
    assert post.has_media is False


def test_post_has_media_true_with_media(make_post, make_media):
    media = make_media()
    post = make_post(author_id="a1", media=[media])
    assert post.has_media is True


def test_post_round_trip(make_post, make_media):
    post = make_post(author_id="a1", media=[make_media()], hashtags=["#foo"])
    restored = Post.model_validate(post.model_dump())
    assert restored.dedup_key == post.dedup_key
    assert restored.hashtags == post.hashtags


def test_post_media_excluded_from_model_dump(make_post, make_media):
    # `media` isn't a `posts` table column (it lives in its own `media`
    # table, linked by post_id) -- `Field(exclude=True)` keeps it out of
    # the payload BaseRepository sends on insert/upsert, so a `model_dump()`
    # round-trip does NOT preserve it (by design, not a regression).
    post = make_post(author_id="a1", media=[make_media()])
    assert post.has_media is True
    assert "media" not in post.model_dump()


# --- Comment ------------------------------------------------------------------


def test_comment_content_rejects_empty_string(make_comment):
    with pytest.raises(ValidationError):
        make_comment(post_id="p1", author_id="a1", content="")


def test_comment_content_rejects_whitespace_only(make_comment):
    with pytest.raises(ValidationError):
        make_comment(post_id="p1", author_id="a1", content="   ")


def test_comment_dedup_key(make_comment):
    comment = make_comment(
        post_id="p1", author_id="a1", platform=PlatformName.INSTAGRAM, platform_comment_id="c1"
    )
    assert comment.dedup_key == "instagram:c1"


def test_comment_is_reply_false_without_parent(make_comment):
    comment = make_comment(post_id="p1", author_id="a1")
    assert comment.is_reply is False


def test_comment_is_reply_true_with_parent(make_comment):
    comment = make_comment(post_id="p1", author_id="a1", parent_comment_id="root-comment")
    assert comment.is_reply is True


def test_comment_round_trip(make_comment):
    comment = make_comment(post_id="p1", author_id="a1", content="nice!")
    restored = Comment.model_validate(comment.model_dump())
    assert restored == comment


def test_reply_requires_parent_comment_id():
    with pytest.raises(ValidationError):
        Reply(
            platform=PlatformName.INSTAGRAM,
            platform_comment_id="c1",
            post_id="p1",
            author_id="a1",
            content="hi",
        )


def test_reply_accepts_parent_comment_id():
    reply = Reply(
        platform=PlatformName.INSTAGRAM,
        platform_comment_id="c1",
        post_id="p1",
        author_id="a1",
        content="hi",
        parent_comment_id="root",
    )
    assert reply.is_reply is True


def test_thread_total_participants(make_comment):
    root = make_comment(post_id="p1", author_id="a1")
    reply1 = make_comment(post_id="p1", author_id="a2", parent_comment_id=root.platform_comment_id)
    reply2 = make_comment(post_id="p1", author_id="a1", parent_comment_id=root.platform_comment_id)
    thread = Thread(root=root, replies=[reply1, reply2])
    assert thread.total_participants == 2


# --- Media ----------------------------------------------------------------


def test_media_url_rejects_non_http_url(make_media):
    with pytest.raises(ValidationError):
        make_media(url="ftp://example.com/file.jpg")


def test_media_url_rejects_relative_path(make_media):
    with pytest.raises(ValidationError):
        make_media(url="/local/path/image.jpg")


def test_media_url_accepts_http(make_media):
    media = make_media(url="http://example.com/image.jpg")
    assert media.url == "http://example.com/image.jpg"


def test_media_url_accepts_https(make_media):
    media = make_media(url="https://example.com/image.jpg")
    assert media.url == "https://example.com/image.jpg"


# --- Hashtag / Mention ------------------------------------------------------


def test_hashtag_tag_normalizes_lowercase_no_hash():
    hashtag = Hashtag(tag="#FooBar")
    assert hashtag.tag == "foobar"


def test_hashtag_tag_strips_whitespace():
    hashtag = Hashtag(tag="  #foo  ")
    assert hashtag.tag == "foo"


def test_mention_username_normalizes_lowercase_no_at():
    mention = Mention(username="@Alice")
    assert mention.username == "alice"


# --- Engagement ----------------------------------------------------------------


def test_engagement_total_engagement_sums_known_signals(make_engagement):
    engagement = make_engagement(likes=10, shares=5, comments_count=2, saves=3)
    assert engagement.total_engagement == 20


def test_engagement_total_engagement_ignores_none_values(make_engagement):
    engagement = make_engagement(likes=10, shares=None, comments_count=None, saves=None)
    assert engagement.total_engagement == 10


def test_engagement_total_engagement_includes_reactions(make_engagement):
    engagement = make_engagement(
        likes=10, shares=0, comments_count=0, saves=0, reactions={"love": 3, "haha": 2}
    )
    assert engagement.total_engagement == 15


def test_engagement_rate_none_when_no_views(make_engagement):
    engagement = make_engagement(views=None)
    assert engagement.engagement_rate is None


def test_engagement_rate_none_when_zero_views(make_engagement):
    engagement = make_engagement(views=0)
    assert engagement.engagement_rate is None


def test_engagement_rate_computed_when_views_present(make_engagement):
    engagement = make_engagement(likes=10, shares=0, comments_count=0, saves=0, views=100)
    assert engagement.engagement_rate == pytest.approx(0.1)


def test_engagement_round_trip(make_engagement):
    engagement = make_engagement(likes=1, views=2, shares=3, comments_count=4, saves=5)
    restored = Engagement.model_validate(engagement.model_dump())
    assert restored.total_engagement == engagement.total_engagement
    assert restored.engagement_rate == engagement.engagement_rate


# --- Channel / Video -------------------------------------------------------------


def test_channel_dedup_key():
    channel = Channel(
        platform=PlatformName.YOUTUBE,
        platform_channel_id="chan1",
        author_id="a1",
        name="Some Channel",
    )
    assert channel.dedup_key == "youtube:chan1"


def test_video_dedup_key():
    video = Video(
        platform=PlatformName.YOUTUBE,
        platform_video_id="vid1",
        channel_id="chan1",
        title="Some Video",
    )
    assert video.dedup_key == "youtube:vid1"


def test_video_has_transcript_false_when_none():
    video = Video(
        platform=PlatformName.YOUTUBE,
        platform_video_id="vid1",
        channel_id="chan1",
        title="Some Video",
        transcript=None,
    )
    assert video.has_transcript is False


def test_video_has_transcript_false_when_blank():
    video = Video(
        platform=PlatformName.YOUTUBE,
        platform_video_id="vid1",
        channel_id="chan1",
        title="Some Video",
        transcript="   ",
    )
    assert video.has_transcript is False


def test_video_has_transcript_true_when_present():
    video = Video(
        platform=PlatformName.YOUTUBE,
        platform_video_id="vid1",
        channel_id="chan1",
        title="Some Video",
        transcript="hello world",
    )
    assert video.has_transcript is True


# --- Conversation ---------------------------------------------------------------


def test_conversation_display_title_defaults_when_no_title():
    conversation = Conversation()
    assert conversation.display_title == "New conversation"


def test_conversation_display_title_uses_title_when_present():
    conversation = Conversation(title="My chat")
    assert conversation.display_title == "My chat"


# --- EmbeddingDocument -----------------------------------------------------------


def _make_embedding_document(**overrides):
    defaults = {
        "source_type": EmbeddingSourceType.POST,
        "source_id": "post-1",
        "platform": PlatformName.INSTAGRAM,
        "text": "hello world",
        "vector": [0.1, 0.2, 0.3],
        "model": "text-embedding-3-small",
        "dimensions": 3,
        "checksum": "abc123",
    }
    defaults.update(overrides)
    return EmbeddingDocument(**defaults)


def test_embedding_document_valid_vector_length():
    doc = _make_embedding_document()
    assert len(doc.vector) == doc.dimensions


def test_embedding_document_rejects_mismatched_vector_length():
    with pytest.raises(ValidationError):
        _make_embedding_document(vector=[0.1, 0.2], dimensions=3)


def test_embedding_document_allows_empty_vector_regardless_of_dimensions():
    doc = _make_embedding_document(vector=[], dimensions=1536)
    assert doc.vector == []


def test_embedding_document_round_trip():
    doc = _make_embedding_document()
    restored = EmbeddingDocument.model_validate(doc.model_dump())
    assert restored == doc


# --- Cross-cutting: ContentType / MediaType usage --------------------------------


def test_post_content_type_accepts_enum_member(make_post):
    post = make_post(author_id="a1", content_type=ContentType.REEL)
    assert post.content_type == ContentType.REEL


def test_media_type_accepts_enum_member(make_media):
    media = make_media(media_type=MediaType.VIDEO)
    assert media.media_type == MediaType.VIDEO


def test_post_posted_at_accepts_datetime(make_post):
    now = datetime.now(UTC)
    post = make_post(author_id="a1", posted_at=now)
    assert post.posted_at == now
