"""Unit tests for the Gradio UI layer (`app/gradio/app.py`,
`app/gradio/chat_tab.py`, `app/gradio/analytics_tab.py`).

`build_app()` must succeed with no `OPENAI_API_KEY`/`SUPABASE_URL`
configured -- service construction (`ChatService`/`Assistant`,
`AnalyticsService`) is either lazy (chat) or lazy-per-repository
(analytics), so laying out the Blocks graph never touches OpenAI/Supabase.
No `.launch()` call is made anywhere in this file.

Most of `chat_tab.py`/`analytics_tab.py` is Gradio event-handler wiring
(`build_chat_tab`/`build_analytics_tab`) or async callbacks that reach
through `_get_chat_service()`/`_analytics_service` into real service/repo
objects -- those are exercised indirectly via `build_app()` (layout only,
no click simulated) rather than here, since driving them would require
either a running Gradio server or faking the module-level service
singletons. The pure, side-effect-free helpers in each module (data
shaping / formatting, with no I/O) are tested directly below.
"""

from __future__ import annotations

import pandas as pd

import gradio as gr
from app.gradio.analytics_tab import (
    _ai_stats_markdown,
    _authors_frame,
    _engagement_frame,
    _hashtags_frame,
    _jobs_frame,
    _platform_frame,
    _records_frame,
)
from app.gradio.app import build_app
from app.gradio.chat_tab import (
    _append_user_message,
    _conversation_choices,
    _extract_text,
    _new_chat,
)
from app.models.pydantic import Engagement
from app.models.pydantic.enums import PlatformName, ScrapeJobStatus


def test_build_app_returns_blocks() -> None:
    """Must work even with no OPENAI_API_KEY/SUPABASE_URL configured, since
    every service (`ChatService`/`Assistant`, `AnalyticsService`) is either
    constructed lazily (chat, on first click) or holds only lazily-connecting
    repositories (analytics) -- laying out the Blocks graph touches neither
    OpenAI nor Supabase.
    """
    blocks = build_app()
    assert isinstance(blocks, gr.Blocks)


def test_build_app_can_be_called_more_than_once() -> None:
    # Guards against any accidental module-level singleton state that would
    # break a second Blocks graph being built in the same process.
    first = build_app()
    second = build_app()
    assert isinstance(first, gr.Blocks)
    assert isinstance(second, gr.Blocks)


# --------------------------------------------------------------------------
# chat_tab.py pure helpers
# --------------------------------------------------------------------------


class TestConversationChoices:
    def test_maps_display_title_and_id(self) -> None:
        from app.models.pydantic import Conversation

        conversations = [
            Conversation(title="First chat"),
            Conversation(title=None),  # falls back to display_title default
        ]

        choices = _conversation_choices(conversations)

        assert choices[0] == ("First chat", str(conversations[0].id))
        assert choices[1] == ("New conversation", str(conversations[1].id))

    def test_empty_list_returns_empty_choices(self) -> None:
        assert _conversation_choices([]) == []


class TestAppendUserMessage:
    def test_appends_message_and_clears_textbox(self) -> None:
        textbox, history = _append_user_message("hello", [])
        assert textbox == ""
        assert history == [{"role": "user", "content": "hello"}]

    def test_preserves_existing_history(self) -> None:
        existing = [{"role": "assistant", "content": "hi there"}]
        textbox, history = _append_user_message("follow up", existing)
        assert textbox == ""
        assert history == [
            {"role": "assistant", "content": "hi there"},
            {"role": "user", "content": "follow up"},
        ]

    def test_blank_message_is_a_no_op(self) -> None:
        # A blank/whitespace-only message leaves history untouched (no user
        # turn appended) and preserves the textbox content so the user can
        # correct it, rather than silently wiping what they typed.
        existing = [{"role": "user", "content": "already here"}]
        textbox, history = _append_user_message("   ", existing)
        assert textbox == "   "
        assert history == existing


class TestExtractText:
    def test_plain_string_passes_through(self) -> None:
        assert _extract_text("hello") == "hello"

    def test_dict_with_text_key(self) -> None:
        assert _extract_text({"text": "hello", "type": "text"}) == "hello"

    def test_list_of_text_parts_joined(self) -> None:
        # This is the real payload shape that crashed `_ask` in production:
        # a direct call to the auto-generated `/call/_ask` API endpoint sent
        # a multimodal-shaped content list instead of the plain string
        # `_append_user_message` always produces internally.
        content = [{"text": "hello world", "type": "text"}]
        assert _extract_text(content) == "hello world"

    def test_dict_missing_text_key_returns_empty_string(self) -> None:
        assert _extract_text({"type": "text"}) == ""

    def test_other_types_stringified(self) -> None:
        assert _extract_text(123) == "123"


def test_new_chat_resets_state() -> None:
    history, conversation_id, dropdown = _new_chat()
    assert history == []
    assert conversation_id is None
    assert isinstance(dropdown, gr.Dropdown)


# --------------------------------------------------------------------------
# analytics_tab.py pure helpers
# --------------------------------------------------------------------------


class TestRecordsFrame:
    def test_empty_records_has_expected_columns_only(self) -> None:
        frame = _records_frame([], ["a", "b"])
        assert list(frame.columns) == ["a", "b"]
        assert len(frame) == 0

    def test_nonempty_records_select_only_expected_columns(self) -> None:
        frame = _records_frame([{"a": 1, "b": 2, "c": 3}], ["a", "b"])
        assert list(frame.columns) == ["a", "b"]
        assert frame.iloc[0].to_dict() == {"a": 1, "b": 2}


class TestHashtagsFrame:
    def test_builds_tag_and_id_columns(self) -> None:
        frame = _hashtags_frame([{"tag": "sunset", "id": "h1"}])
        assert list(frame.columns) == ["tag", "id"]
        assert frame.iloc[0]["tag"] == "sunset"

    def test_empty(self) -> None:
        frame = _hashtags_frame([])
        assert list(frame.columns) == ["tag", "id"]
        assert frame.empty


class TestAuthorsFrame:
    def test_builds_username_platform_follower_count(self, make_author: object) -> None:
        author = make_author(username="alice", follower_count=42)
        frame = _authors_frame([author])
        assert list(frame.columns) == ["username", "platform", "follower_count"]
        row = frame.iloc[0]
        assert row["username"] == "alice"
        assert row["platform"] == PlatformName.INSTAGRAM
        assert row["follower_count"] == 42

    def test_empty(self) -> None:
        frame = _authors_frame([])
        assert list(frame.columns) == ["username", "platform", "follower_count"]
        assert frame.empty


class TestEngagementFrame:
    def test_builds_post_id_likes_views_total_engagement(self, make_engagement: object) -> None:
        engagement = make_engagement(post_id="post-1", likes=10, views=100)
        frame = _engagement_frame([engagement])
        assert list(frame.columns) == ["post_id", "likes", "views", "total_engagement"]
        row = frame.iloc[0]
        assert row["post_id"] == "post-1"
        assert row["likes"] == 10
        assert row["views"] == 100
        assert row["total_engagement"] == engagement.total_engagement

    def test_empty(self) -> None:
        frame = _engagement_frame([])
        assert list(frame.columns) == ["post_id", "likes", "views", "total_engagement"]
        assert frame.empty


class TestJobsFrame:
    def test_builds_expected_columns(self) -> None:
        from app.repositories.scrape_job_repository import ScrapeJob

        job = ScrapeJob(
            platform=PlatformName.TWITTER,
            job_type="posts",
            status=ScrapeJobStatus.SUCCEEDED,
            target="@someone",
            records_scraped=25,
        )
        frame = _jobs_frame([job])
        assert list(frame.columns) == [
            "platform",
            "job_type",
            "status",
            "target",
            "records_scraped",
            "created_at",
        ]
        row = frame.iloc[0]
        assert row["platform"] == PlatformName.TWITTER
        assert row["records_scraped"] == 25

    def test_empty(self) -> None:
        frame = _jobs_frame([])
        assert list(frame.columns) == [
            "platform",
            "job_type",
            "status",
            "target",
            "records_scraped",
            "created_at",
        ]
        assert frame.empty


class TestPlatformFrame:
    def test_builds_platform_and_count_columns(self) -> None:
        frame = _platform_frame({"instagram": 5, "twitter": 3})
        assert list(frame.columns) == ["platform", "count"]
        assert frame["platform"].tolist() == ["instagram", "twitter"]
        assert frame["count"].tolist() == [5, 3]

    def test_empty_distribution(self) -> None:
        frame = _platform_frame({})
        assert list(frame.columns) == ["platform", "count"]
        assert frame.empty


class TestAiStatsMarkdown:
    def test_formats_stats_with_latency(self) -> None:
        markdown = _ai_stats_markdown({"total_queries": 12, "avg_latency_ms": 250.5})
        assert "12" in markdown
        assert "250.5 ms" in markdown

    def test_formats_missing_latency_as_na(self) -> None:
        markdown = _ai_stats_markdown({})
        assert "total queries: 0" in markdown
        assert "n/a" in markdown


def test_engagement_and_dataframe_imports_are_sane() -> None:
    # Sanity check that the helpers really return real DataFrames (not some
    # other pandas-like object) so `gr.Dataframe` would accept them.
    assert isinstance(_platform_frame({"instagram": 1}), pd.DataFrame)
    assert isinstance(Engagement(likes=1), Engagement)
