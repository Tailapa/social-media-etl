"""Gradio "Analytics" tab: a read-only dashboard over `AnalyticsService`.

Everything here is populated on demand by the "Refresh" button rather than at
Blocks-build time, so importing/building this module never touches the
database — important because the app must still build (though not
successfully *refresh*) with no Supabase credentials configured, e.g. in a
bare dev checkout.
"""

from __future__ import annotations

from typing import Any

import pandas as pd

import gradio as gr
from app.logging import get_logger
from app.services.analytics_service import AnalyticsService

logger = get_logger(__name__)

# One service instance shared by every callback/session — see chat_tab.py for
# the same reasoning (repo clients are cheap to hold, not to recreate).
_analytics_service = AnalyticsService()

_HASHTAGS_COLUMNS = ["tag", "id"]
_AUTHORS_COLUMNS = ["username", "platform", "follower_count"]
_ENGAGEMENT_COLUMNS = ["post_id", "likes", "views", "total_engagement"]
_JOBS_COLUMNS = ["platform", "job_type", "status", "target", "records_scraped", "created_at"]


def _records_frame(records: list[dict[str, Any]], columns: list[str]) -> pd.DataFrame:
    """Build a `gr.Dataframe`-ready frame that always has the expected
    columns, even when `records` is empty (an empty `pd.DataFrame([])` has no
    columns at all, which renders as a blank table with no headers).
    """
    if not records:
        return pd.DataFrame(columns=columns)
    return pd.DataFrame(records)[columns]


def _hashtags_frame(hashtags: list[dict[str, Any]]) -> pd.DataFrame:
    return _records_frame(hashtags, _HASHTAGS_COLUMNS)


def _authors_frame(authors: list[Any]) -> pd.DataFrame:
    return _records_frame(
        [
            {"username": a.username, "platform": a.platform, "follower_count": a.follower_count}
            for a in authors
        ],
        _AUTHORS_COLUMNS,
    )


def _engagement_frame(posts: list[Any]) -> pd.DataFrame:
    return _records_frame(
        [
            {
                "post_id": p.post_id,
                "likes": p.likes,
                "views": p.views,
                "total_engagement": p.total_engagement,
            }
            for p in posts
        ],
        _ENGAGEMENT_COLUMNS,
    )


def _jobs_frame(jobs: list[Any]) -> pd.DataFrame:
    return _records_frame(
        [
            {
                "platform": j.platform,
                "job_type": j.job_type,
                "status": j.status,
                "target": j.target,
                "records_scraped": j.records_scraped,
                "created_at": j.created_at,
            }
            for j in jobs
        ],
        _JOBS_COLUMNS,
    )


def _platform_frame(distribution: dict[str, int]) -> pd.DataFrame:
    return pd.DataFrame(
        {"platform": list(distribution.keys()), "count": list(distribution.values())}
    )


def _ai_stats_markdown(stats: dict[str, Any]) -> str:
    avg_latency = stats.get("avg_latency_ms")
    avg_latency_text = f"{avg_latency} ms" if avg_latency is not None else "n/a"
    return (
        f"**AI query stats** — total queries: {stats.get('total_queries', 0)}, "
        f"avg latency: {avg_latency_text}"
    )


_EMPTY_RESULT: tuple[Any, ...] = (
    gr.update(visible=False),
    0,
    0,
    _platform_frame({}),
    _hashtags_frame([]),
    _authors_frame([]),
    _engagement_frame([]),
    _jobs_frame([]),
    _ai_stats_markdown({}),
)


async def _refresh() -> tuple[Any, ...]:
    """Fetch `AnalyticsService().dashboard_summary()` and populate every
    tile. A DB-connectivity failure (e.g. no Supabase credentials configured,
    the default in this dev environment) must show a friendly inline banner
    instead of a raw traceback.
    """
    try:
        summary = await _analytics_service.dashboard_summary()
    except Exception as exc:  # noqa: BLE001 - any backend failure is recoverable in the UI
        logger.exception("dashboard_summary failed")
        banner = gr.update(value=f"Could not load analytics: {exc}", visible=True)
        return (banner, *_EMPTY_RESULT[1:])

    return (
        gr.update(visible=False),
        summary["total_posts"],
        summary["total_comments"],
        _platform_frame(summary["platform_distribution"]),
        _hashtags_frame(summary["trending_hashtags"]),
        _authors_frame(summary["most_active_authors"]),
        _engagement_frame(summary["top_engagement_posts"]),
        _jobs_frame(summary["recent_scrape_jobs"]),
        _ai_stats_markdown(summary["ai_query_stats"]),
    )


def build_analytics_tab() -> None:
    """Lay out the Analytics tab. Must be called inside an open `gr.Blocks()`."""
    status_banner = gr.Markdown(visible=False)
    refresh_btn = gr.Button("Refresh", variant="primary")

    with gr.Row():
        total_posts_num = gr.Number(label="Total posts", interactive=False)
        total_comments_num = gr.Number(label="Total comments", interactive=False)

    platform_plot = gr.BarPlot(x="platform", y="count", label="Platform distribution", height=300)

    with gr.Row():
        hashtags_df = gr.Dataframe(label="Trending hashtags", interactive=False)
        authors_df = gr.Dataframe(label="Most active authors", interactive=False)

    with gr.Row():
        engagement_df = gr.Dataframe(label="Top engagement posts", interactive=False)
        jobs_df = gr.Dataframe(label="Recent scrape jobs", interactive=False)

    ai_stats_md = gr.Markdown()

    outputs = [
        status_banner,
        total_posts_num,
        total_comments_num,
        platform_plot,
        hashtags_df,
        authors_df,
        engagement_df,
        jobs_df,
        ai_stats_md,
    ]
    refresh_btn.click(_refresh, inputs=None, outputs=outputs)
