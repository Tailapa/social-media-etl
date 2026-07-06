"""Embedding provider abstraction.

`EmbeddingProvider` is a `Protocol` (structural typing) rather than an ABC so
swapping providers (OpenAI -> a local sentence-transformers model, say)
never requires touching `app.embeddings.service` — it only needs an object
with an `embed_texts` coroutine and a `dimensions`/`model_name` pair.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from openai import AsyncOpenAI

from app.config import get_settings
from app.logging import get_logger
from app.utils.exceptions import EmbeddingError
from app.utils.retry import with_retry

logger = get_logger(__name__)


@runtime_checkable
class EmbeddingProvider(Protocol):
    model_name: str
    dimensions: int

    async def embed_texts(self, texts: list[str]) -> list[list[float]]:
        """Return one embedding vector per input text, same order as input."""
        ...


class OpenAIEmbeddingProvider:
    """Default provider, backed by the OpenAI embeddings API."""

    def __init__(self, model_name: str | None = None, dimensions: int | None = None) -> None:
        settings = get_settings()
        self.model_name = model_name or settings.openai_embedding_model
        self.dimensions = dimensions or settings.embedding_dimensions
        self._client = AsyncOpenAI(api_key=settings.openai_api_key.get_secret_value())

    @with_retry(exceptions=(Exception,), max_attempts=3)
    async def embed_texts(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        try:
            response = await self._client.embeddings.create(model=self.model_name, input=texts)
        except Exception as exc:
            raise EmbeddingError(
                f"OpenAI embedding request failed: {exc}", context={"count": len(texts)}
            ) from exc
        return [item.embedding for item in response.data]
