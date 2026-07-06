"""Embedding generation + storage service.

Implements the "Embedding Pipeline" stage of the ingestion flow: takes
embeddable text (post captions, comments, video transcripts, ...) and
produces a `documents` row (source-of-truth text, keyword-searchable) plus
an `embeddings` row (vector, semantically-searchable), skipping re-embedding
when the text hasn't changed since the last run.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

from app.embeddings.providers import EmbeddingProvider, OpenAIEmbeddingProvider
from app.logging import get_logger
from app.models.pydantic.enums import EmbeddingSourceType, PlatformName
from app.repositories.embedding_repository import (
    Document,
    DocumentRepository,
    EmbeddingRepository,
    EmbeddingRow,
)

logger = get_logger(__name__)


@dataclass(slots=True, frozen=True)
class EmbeddableItem:
    """One unit of text to embed, tied back to its source record."""

    source_type: EmbeddingSourceType
    source_id: str
    platform: PlatformName
    text: str
    metadata: dict | None = None


def checksum_of(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class EmbeddingService:
    """Batch-oriented embedding pipeline used by ingestion after every
    scrape, and available standalone for backfills (`scripts/backfill_embeddings.py`).
    """

    def __init__(
        self,
        provider: EmbeddingProvider | None = None,
        document_repo: DocumentRepository | None = None,
        embedding_repo: EmbeddingRepository | None = None,
    ) -> None:
        self.provider = provider or OpenAIEmbeddingProvider()
        self.document_repo = document_repo or DocumentRepository()
        self.embedding_repo = embedding_repo or EmbeddingRepository()

    async def embed_batch(self, items: list[EmbeddableItem]) -> int:
        """Embed and persist every item, skipping ones whose content hasn't
        changed since the last run (checksum match) and empty/blank text.

        Returns the number of items actually re-embedded (network calls
        avoided count as skipped, not embedded).
        """
        pending: list[EmbeddableItem] = []
        checksums: dict[str, str] = {}

        for item in items:
            text = item.text.strip()
            if not text:
                continue
            checksum = checksum_of(text)
            existing = await self.embedding_repo.get_by_checksum(
                item.source_id, item.source_type.value, self.provider.model_name
            )
            if existing is not None and existing.checksum == checksum:
                logger.debug(
                    "Skipping unchanged embedding",
                    source_type=item.source_type,
                    source_id=item.source_id,
                )
                continue
            pending.append(item)
            checksums[item.source_id] = checksum

        if not pending:
            return 0

        vectors = await self.provider.embed_texts([item.text.strip() for item in pending])

        documents = [
            Document(
                source_type=item.source_type,
                source_id=item.source_id,
                platform=item.platform,
                content=item.text.strip(),
                metadata=item.metadata or {},
            )
            for item in pending
        ]
        persisted_documents = await self.document_repo.bulk_upsert(
            documents, on_conflict="source_type,source_id"
        )

        # Key off the *persisted* rows, not the locally-constructed ones —
        # the DB assigns/keeps the authoritative `id` on upsert (see
        # BaseRepository._serialize_for_upsert), which will differ from the
        # client-generated one on every update of an already-existing document.
        # Note: `Document.source_type` reads back as a plain `str`, not the
        # `EmbeddingSourceType` enum, because `BaseSchema` sets
        # `use_enum_values=True` — only `EmbeddableItem` (a plain dataclass,
        # not a Pydantic model) keeps the real enum, hence `.value` below.
        document_ids = {(str(doc.source_type), doc.source_id): doc for doc in persisted_documents}
        embedding_rows = []
        for item, vector in zip(pending, vectors, strict=True):
            document = document_ids[(item.source_type.value, item.source_id)]
            embedding_rows.append(
                EmbeddingRow(
                    document_id=str(document.id),
                    source_type=item.source_type,
                    source_id=item.source_id,
                    platform=item.platform,
                    model=self.provider.model_name,
                    dimensions=self.provider.dimensions,
                    checksum=checksums[item.source_id],
                    vector=vector,
                    metadata=item.metadata or {},
                )
            )
        await self.embedding_repo.bulk_upsert_embeddings(embedding_rows)
        logger.info("Embedded batch", count=len(embedding_rows))
        return len(embedding_rows)

    async def embed_one(self, item: EmbeddableItem) -> int:
        return await self.embed_batch([item])
