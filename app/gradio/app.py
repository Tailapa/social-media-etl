"""Entry point for the Gradio UI: wires the Chat and Analytics tabs into a
single `gr.Blocks` app.

Kept deliberately thin — `build_app()` only lays out tabs; all business
logic lives in `app.services` and is invoked from the tab-specific callback
modules (`chat_tab.py`, `analytics_tab.py`).
"""

from __future__ import annotations

import gradio as gr
from app.gradio.analytics_tab import build_analytics_tab
from app.gradio.chat_tab import build_chat_tab


def build_app() -> gr.Blocks:
    """Build (but do not launch) the full Blocks app — safe to call with no
    Supabase/OpenAI credentials configured, since layout never hits the
    database; only button clicks do.

    `theme` is passed to `launch()` rather than the `Blocks` constructor: in
    this Gradio version the constructor still accepts it for backwards
    compatibility but emits a `UserWarning` telling you to move it — see
    `main()`.
    """
    with gr.Blocks(title="Social Media Intelligence Platform") as blocks, gr.Tabs():
        with gr.Tab("Chat"):
            build_chat_tab()
        with gr.Tab("Analytics"):
            build_analytics_tab()
    return blocks


def main() -> None:
    # server_name/server_port default to None, which Gradio resolves from
    # GRADIO_SERVER_NAME/GRADIO_SERVER_PORT env vars (falling back to
    # 127.0.0.1:7860) -- the Dockerfile sets GRADIO_SERVER_NAME=0.0.0.0 so
    # the app is reachable from outside the container without hardcoding
    # that bind address for local (non-container) runs too.
    build_app().launch(theme=gr.themes.Soft())


if __name__ == "__main__":
    main()
