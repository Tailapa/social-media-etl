"""Gradio "Chat" tab: a conversational UI over `ChatService`.

The tab is UI-only — every question, history load, search, and export goes
through `ChatService` so this layer never touches the AI/retrieval/DB layers
directly (the same boundary `ChatService` itself documents). All state that
needs to survive between callbacks (the active conversation id) lives in a
`gr.State`, never in module globals, since Gradio serves every browser
session from the same Python process.
"""

from __future__ import annotations

import tempfile
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import gradio as gr
from app.logging import get_logger
from app.models.pydantic import Conversation
from app.services.chat_service import ChatService

logger = get_logger(__name__)

# One service instance shared by every callback/session — it only holds repo
# clients, no per-conversation state, so reuse is safe and avoids re-creating
# DB/AI clients on every click. Construction is deferred (see
# `_get_chat_service`) rather than done here at import time, because
# `ChatService` builds an `Assistant`, which eagerly constructs an OpenAI
# client and raises if no API key is configured — the Blocks graph must still
# build in a bare dev checkout; only an actual chat turn needs credentials.
_chat_service: ChatService | None = None

# `gr.Chatbot` (this Gradio version's only supported format) expects a list
# of {"role", "content"} dicts — the old tuple format no longer exists.
ChatHistory = list[dict[str, str]]


def _get_chat_service() -> ChatService:
    global _chat_service
    if _chat_service is None:
        _chat_service = ChatService()
    return _chat_service


def _conversation_choices(conversations: list[Conversation]) -> list[tuple[str, str]]:
    """(label, value) pairs for the sidebar picker — value is the row id."""
    return [(c.display_title, str(c.id)) for c in conversations]


async def _refresh_conversations() -> gr.Dropdown:
    conversations = await _get_chat_service().list_conversations()
    return gr.Dropdown(choices=_conversation_choices(conversations), value=None)


async def _search_conversations(query: str) -> gr.Dropdown:
    if not query.strip():
        return await _refresh_conversations()
    conversations = await _get_chat_service().search_conversations(query)
    return gr.Dropdown(choices=_conversation_choices(conversations), value=None)


async def _load_conversation(conversation_id: str | None) -> tuple[ChatHistory, str | None]:
    """Selecting a conversation in the sidebar loads its full history."""
    if not conversation_id:
        return [], None
    messages = await _get_chat_service().get_history(conversation_id)
    history: ChatHistory = [{"role": m.role, "content": m.content} for m in messages]
    return history, conversation_id


def _new_chat() -> tuple[ChatHistory, None, gr.Dropdown]:
    """ "New chat" just resets local UI state — `ChatService.ask` lazily
    creates the actual conversation row on the first question.
    """
    return [], None, gr.Dropdown(value=None)


async def _clear_chat(conversation_id: str | None) -> tuple[ChatHistory, None]:
    """ "Clear chat" archives the conversation server-side (soft-delete) and
    resets the visible chat to a blank slate.
    """
    if conversation_id:
        await _get_chat_service().clear_conversation(conversation_id)
    return [], None


def _append_user_message(message: str, history: ChatHistory) -> tuple[str, ChatHistory]:
    """Echo the user's message immediately so the UI feels responsive while
    the assistant call is in flight; clears the textbox on a real submission,
    but leaves a blank/whitespace-only input in place so the user can correct
    it instead of having it silently wiped.
    """
    if not message.strip():
        return message, history
    return "", [*history, {"role": "user", "content": message}]


def _extract_text(content: Any) -> str:
    """Normalize a chat message's `content` to plain text.

    `_append_user_message` always stores a plain string, but a direct API
    call to this event (Gradio auto-generates a `/call/_ask` endpoint for
    every wired function) can send a multimodal-shaped payload instead
    (e.g. `[{"text": "...", "type": "text"}]`) — this must degrade
    gracefully rather than let a `Conversation(title=...)` validation error
    bubble up as a crashed turn.
    """
    if isinstance(content, str):
        return content
    if isinstance(content, dict):
        return str(content.get("text", ""))
    if isinstance(content, list):
        return " ".join(
            str(part.get("text", "")) if isinstance(part, dict) else str(part) for part in content
        )
    return str(content)


async def _ask(
    history: ChatHistory, conversation_id: str | None
) -> AsyncIterator[tuple[ChatHistory, str | None]]:
    """Call `ChatService.ask` for the last user turn and reveal the answer.

    `ChatService.ask` is not a token-streamer — it awaits one full completion
    — so this yields the placeholder once and then the full answer, rather
    than faking a token-by-token effect. True token streaming would require
    `Assistant`/the underlying OpenAI call to support streaming, which is a
    documented future improvement, not something to fake here.
    """
    if not history or history[-1]["role"] != "user":
        return
    question = _extract_text(history[-1]["content"])

    thinking = [*history, {"role": "assistant", "content": "_Thinking..._"}]
    yield thinking, conversation_id

    try:
        reply = await _get_chat_service().ask(question, conversation_id=conversation_id)
    except Exception as exc:  # noqa: BLE001 - must never crash the UI
        logger.exception("chat_service.ask failed")
        error_history = [
            *history,
            {"role": "assistant", "content": f"Sorry, something went wrong: {exc}"},
        ]
        yield error_history, conversation_id
        return

    final_history = [*history, {"role": "assistant", "content": reply.content}]
    yield final_history, reply.conversation_id


async def _export_conversation(conversation_id: str | None) -> gr.File | None:
    """Render the conversation as Markdown and offer it as a download.

    Written to a `tempfile`-managed path (never a hardcoded location) since
    `gr.File` needs an actual path on disk to serve.
    """
    if not conversation_id:
        gr.Warning("Start or select a conversation before exporting.")
        return None
    markdown = await _get_chat_service().export_conversation(conversation_id)
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".md", delete=False, encoding="utf-8"
    ) as handle:
        handle.write(markdown)
        export_path = Path(handle.name)
    return gr.File(value=str(export_path), visible=True)


def build_chat_tab() -> None:
    """Lay out the Chat tab. Must be called inside an open `gr.Blocks()`."""
    conversation_id = gr.State(value=None)

    with gr.Row(equal_height=True):
        with gr.Sidebar(label="Conversations", position="left"):
            search_box = gr.Textbox(
                label="Search",
                placeholder="Search conversations by title...",
                show_label=False,
            )
            conversation_picker = gr.Dropdown(
                label="Recent conversations",
                choices=[],
                value=None,
                interactive=True,
            )
            refresh_btn = gr.Button("Refresh")
            export_btn = gr.Button("Export as Markdown")
            export_file = gr.File(label="Exported conversation", visible=False)

        with gr.Column(scale=4):
            chatbot = gr.Chatbot(label="Assistant", height=500)
            with gr.Row():
                message_box = gr.Textbox(
                    label="Message",
                    placeholder="Ask about your scraped data...",
                    scale=5,
                    show_label=False,
                )
                send_btn = gr.Button("Send", variant="primary", scale=1)
            with gr.Row():
                new_chat_btn = gr.Button("New chat")
                clear_chat_btn = gr.Button("Clear chat")

    # Wire "ask" both to the Send button and to pressing Enter in the textbox.
    ask_inputs = [message_box, chatbot]
    for trigger in (message_box.submit, send_btn.click):
        trigger(_append_user_message, inputs=ask_inputs, outputs=[message_box, chatbot]).then(
            _ask, inputs=[chatbot, conversation_id], outputs=[chatbot, conversation_id]
        )

    new_chat_btn.click(
        _new_chat, inputs=None, outputs=[chatbot, conversation_id, conversation_picker]
    )
    clear_chat_btn.click(_clear_chat, inputs=[conversation_id], outputs=[chatbot, conversation_id])

    refresh_btn.click(_refresh_conversations, inputs=None, outputs=[conversation_picker])
    search_box.submit(_search_conversations, inputs=[search_box], outputs=[conversation_picker])
    conversation_picker.change(
        _load_conversation, inputs=[conversation_picker], outputs=[chatbot, conversation_id]
    )
    export_btn.click(_export_conversation, inputs=[conversation_id], outputs=[export_file])
