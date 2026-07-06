"""Repositories for the `documents` / `embeddings` tables.

Two tables, two repositories, because they have distinct write patterns: a
`Document` is the human-readable source-of-truth text (also keyword-searched
via `search_vector`), while an `EmbeddingRow` is the vector derived from it —
the embeddings pipeline writes both, the retrieval layer mostly reads
`embeddings` via the `match_embeddings` RPC (see `app/retrieval`).
"""

from __future__ import annotations

import asyncio
from typing import Any

from pydantic import BaseModel, Field, field_validator

from app.database.supabase_client import get_supabase_client
from app.models.pydantic.base import BaseSchema, CreatedAtMixin, IdentifiedMixin
from app.models.pydantic.enums import EmbeddingSourceType, PlatformName
from app.repositories.base import BaseRepository


class Document(IdentifiedMixin, CreatedAtMixin, BaseSchema):
    """Mirrors the `documents` table: the source-of-truth text for a chunk
    of embeddable content, independent of which embedding model vectorized it.
    """

    source_type: EmbeddingSourceType
    source_id: str
    platform: PlatformName
    content: str
    metadata: dict = Field(default_factory=dict)


class EmbeddingRow(BaseModel):
    """Mirrors the `embeddings` table row (vector storage linked to a Document)."""

    document_id: str
    source_type: EmbeddingSourceType
    source_id: str
    platform: PlatformName
    model: str
    dimensions: int
    checksum: str
    vector: list[float]
    metadata: dict = Field(default_factory=dict)

    @field_validator("vector", mode="before")
    @classmethod
    def _parse_pgvector_string(cls, value: Any) -> Any:
        """PostgREST serializes the `vector` column back as its Postgres
        array-literal string form (e.g. `"[0.01,-0.02,...]"`), not a JSON
        array, so a row read back after an insert/upsert needs this parsed
        before pydantic's `list[float]` validation runs.
        """
        if isinstance(value, str):
            return [float(x) for x in value.strip("[]").split(",")]
        return value


class DocumentRepository(BaseRepository[Document]):
    table_name = "documents"
    model = Document

    async def get_by_source(self, source_type: str, source_id: str) -> Document | None:
        results = await self.list_all(
            filters={"source_type": source_type, "source_id": source_id}, limit=1
        )
        return results[0] if results else None

    async def upsert_document(self, document: Document) -> Document:
        return await self.upsert(document, on_conflict="source_type,source_id")


class EmbeddingRepository(BaseRepository[EmbeddingRow]):
    table_name = "embeddings"
    model = EmbeddingRow

    async def get_by_checksum(
        self, source_id: str, source_type: str, model: str
    ) -> EmbeddingRow | None:
        """Look up an existing embedding for (source, model) — used to skip
        re-embedding unchanged content (checksum comparison happens in the
        embeddings service; this just fetches the current row).
        """
        results = await self.list_all(
            filters={"source_id": source_id, "source_type": source_type, "model": model}, limit=1
        )
        return results[0] if results else None

    async def upsert_embedding(self, embedding: EmbeddingRow) -> EmbeddingRow:
        return await self.upsert(embedding, on_conflict="source_id,source_type,model")

    async def bulk_upsert_embeddings(self, embeddings: list[EmbeddingRow]) -> list[EmbeddingRow]:
        return await self.bulk_upsert(embeddings, on_conflict="source_id,source_type,model")

    async def match(
        self,
        query_vector: list[float],
        *,
        match_count: int = 10,
        platform: str | None = None,
    ) -> list[dict[str, Any]]:
        """Semantic similarity search via the `match_embeddings` Postgres RPC
        (see migrations/0004) — cosine distance computed in the database so
        we never pull the full vector table over the wire.
        """

        def _run() -> Any:
            return (
                get_supabase_client()
                .rpc(
                    "match_embeddings",
                    {
                        "query_embedding": query_vector,
                        "match_count": match_count,
                        "filter_platform": platform,
                    },
                )
                .execute()
            )

        response = await asyncio.to_thread(_run)
        return response.data
