"""Thin orchestration layer between the Gradio chat UI and `app.ai.Assistant`
+ the conversation repositories — the UI never touches the AI/retrieval/DB
layers directly.
"""

from __future__ import annotations

from app.ai.assistant import Assistant
from app.models.pydantic import ChatMessage, Conversation
from app.repositories.conversation_repository import ConversationRepository
from app.repositories.message_repository import MessageRepository


class ChatService:
    def __init__(
        self,
        assistant: Assistant | None = None,
        conversation_repo: ConversationRepository | None = None,
        message_repo: MessageRepository | None = None,
    ) -> None:
        self.assistant = assistant or Assistant()
        self.conversation_repo = conversation_repo or ConversationRepository()
        self.message_repo = message_repo or MessageRepository()

    async def ask(self, question: str, *, conversation_id: str | None = None) -> ChatMessage:
        return await self.assistant.ask(question, conversation_id=conversation_id)

    async def new_conversation(self, title: str | None = None) -> Conversation:
        return await self.conversation_repo.create(Conversation(title=title))

    async def list_conversations(self, *, limit: int = 50) -> list[Conversation]:
        return await self.conversation_repo.list_all(
            order_by="updated_at", descending=True, limit=limit
        )

    async def search_conversations(self, query: str, *, limit: int = 20) -> list[Conversation]:
        return await self.conversation_repo.search_by_title(query, limit=limit)

    async def get_history(self, conversation_id: str) -> list[ChatMessage]:
        return await self.message_repo.by_conversation(conversation_id)

    async def clear_conversation(self, conversation_id: str) -> None:
        await self.conversation_repo.soft_delete(conversation_id)

    async def export_conversation(self, conversation_id: str) -> str:
        """Render a conversation as Markdown for the Gradio "export" button."""
        conversation = await self.conversation_repo.require_by_id(conversation_id)
        messages = await self.get_history(conversation_id)
        lines = [f"# {conversation.display_title}", ""]
        for message in messages:
            speaker = "**You**" if message.role == "user" else "**Assistant**"
            lines.append(f"{speaker} ({message.created_at.isoformat()}):")
            lines.append(message.content)
            if message.sources:
                lines.append(f"\n_Sources: {', '.join(message.sources)}_")
            lines.append("")
        return "\n".join(lines)
