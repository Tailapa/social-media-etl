"""Unit tests for app/embeddings/service.py's EmbeddingService.

`Document` (app/repositories/embedding_repository.py) extends `BaseSchema`,
whose `model_config` sets `use_enum_values=True` — so `Document.source_type`
reads back as a plain `str`, never an `EmbeddingSourceType` enum member (see
`test_document_source_type_is_plain_str_due_to_use_enum_values` below).
`embed_batch` accounts for this by normalizing both sides of its persisted
document lookup to `str` (see the code comment at
`app/embeddings/service.py`'s `document_ids` construction) — the tests below
exercise that linkage explicitly via `FakeDocumentRepo`, which deliberately
returns a *different* id than the client-generated one, matching what a
real upsert-on-conflict does.
"""

from __future__ import annotations

import uuid

from app.embeddings.service import EmbeddableItem, EmbeddingService, checksum_of
from app.models.pydantic.enums import EmbeddingSourceType, PlatformName
from app.repositories.embedding_repository import Document, EmbeddingRow


class FakeProvider:
    """Fake EmbeddingProvider: returns one fixed-length vector per text."""

    model_name = "fake-model"
    dimensions = 3

    def __init__(self):
        self.calls: list[list[str]] = []

    async def embed_texts(self, texts: list[str]) -> list[list[float]]:
        self.calls.append(texts)
        return [[0.1, 0.2, 0.3] for _ in texts]


class FakeDocumentRepo:
    """Fake DocumentRepository.bulk_upsert: returns Documents with a NEW
    server-assigned id, different from any client-generated id, to prove the
    service links embeddings off the *persisted* document id.
    """

    def __init__(self):
        self.bulk_upsert_calls: list[list[Document]] = []

    async def bulk_upsert(self, documents: list[Document], *, on_conflict: str) -> list[Document]:
        self.bulk_upsert_calls.append(documents)
        persisted = []
        for doc in documents:
            persisted.append(
                Document(
                    id=uuid.uuid4(),  # deliberately different from doc.id
                    source_type=doc.source_type,
                    source_id=doc.source_id,
                    platform=doc.platform,
                    content=doc.content,
                    metadata=doc.metadata,
                )
            )
        return persisted


class FakeEmbeddingRepo:
    def __init__(self, existing: dict[tuple[str, str, str], EmbeddingRow] | None = None):
        self._existing = existing or {}
        self.bulk_upsert_calls: list[list[EmbeddingRow]] = []

    async def get_by_checksum(
        self, source_id: str, source_type: str, model: str
    ) -> EmbeddingRow | None:
        return self._existing.get((source_id, source_type, model))

    async def bulk_upsert_embeddings(self, embeddings: list[EmbeddingRow]) -> list[EmbeddingRow]:
        self.bulk_upsert_calls.append(embeddings)
        return embeddings


def _make_item(**overrides) -> EmbeddableItem:
    defaults = {
        "source_type": EmbeddingSourceType.POST,
        "source_id": "post-1",
        "platform": PlatformName.INSTAGRAM,
        "text": "hello world",
        "metadata": None,
    }
    defaults.update(overrides)
    return EmbeddableItem(**defaults)


def _make_service(provider=None, document_repo=None, embedding_repo=None) -> EmbeddingService:
    return EmbeddingService(
        provider=provider or FakeProvider(),
        document_repo=document_repo or FakeDocumentRepo(),
        embedding_repo=embedding_repo or FakeEmbeddingRepo(),
    )


# ============================================================================
# checksum_of
# ============================================================================


def test_checksum_of_is_deterministic():
    assert checksum_of("hello") == checksum_of("hello")


def test_checksum_of_differs_for_different_text():
    assert checksum_of("hello") != checksum_of("world")


def test_checksum_of_is_sha256_hex_digest():
    import hashlib

    assert checksum_of("hello") == hashlib.sha256(b"hello").hexdigest()


# ============================================================================
# embed_batch: skip conditions
# ============================================================================


async def test_embed_batch_skips_empty_text():
    provider = FakeProvider()
    service = _make_service(provider=provider)
    item = _make_item(text="")
    count = await service.embed_batch([item])
    assert count == 0
    assert provider.calls == []


async def test_embed_batch_skips_blank_text():
    provider = FakeProvider()
    service = _make_service(provider=provider)
    item = _make_item(text="   ")
    count = await service.embed_batch([item])
    assert count == 0
    assert provider.calls == []


async def test_embed_batch_skips_unchanged_checksum():
    text = "unchanged text"
    checksum = checksum_of(text)
    existing_row = EmbeddingRow(
        document_id=str(uuid.uuid4()),
        source_type=EmbeddingSourceType.POST,
        source_id="post-1",
        platform=PlatformName.INSTAGRAM,
        model="fake-model",
        dimensions=3,
        checksum=checksum,
        vector=[0.1, 0.2, 0.3],
    )
    embedding_repo = FakeEmbeddingRepo(existing={("post-1", "post", "fake-model"): existing_row})
    provider = FakeProvider()
    document_repo = FakeDocumentRepo()
    service = _make_service(
        provider=provider, document_repo=document_repo, embedding_repo=embedding_repo
    )

    item = _make_item(text=text)
    count = await service.embed_batch([item])

    assert count == 0
    assert provider.calls == []
    assert document_repo.bulk_upsert_calls == []
    assert embedding_repo.bulk_upsert_calls == []


async def test_embed_batch_returns_zero_for_empty_item_list():
    service = _make_service()
    assert await service.embed_batch([]) == 0


# ============================================================================
# embed_batch: happy path / document_id linkage
# ============================================================================


def test_document_source_type_is_plain_str_due_to_use_enum_values():
    """Root-cause proof for the bug documented in the module docstring:
    `Document.source_type` is coerced to a plain `str` on construction
    (BaseSchema's `use_enum_values=True`), so it never has a `.value`
    attribute — reproduced here directly against the real `Document` model,
    independent of any fake used elsewhere in this file.
    """
    doc = Document(
        source_type=EmbeddingSourceType.POST,
        source_id="x",
        platform=PlatformName.INSTAGRAM,
        content="hi",
    )
    assert isinstance(doc.source_type, str)
    assert not hasattr(doc.source_type, "value")


async def test_embed_batch_embeds_changed_text_and_links_persisted_document_id():
    provider = FakeProvider()
    document_repo = FakeDocumentRepo()
    embedding_repo = FakeEmbeddingRepo()
    service = _make_service(
        provider=provider, document_repo=document_repo, embedding_repo=embedding_repo
    )

    item = _make_item(text="new content")
    count = await service.embed_batch([item])

    assert count == 1
    assert provider.calls == [["new content"]]
    assert len(document_repo.bulk_upsert_calls) == 1
    assert len(embedding_repo.bulk_upsert_calls) == 1
    embedded_row = embedding_repo.bulk_upsert_calls[0][0]
    # The embedding's document_id must be the *persisted* document's id
    # (assigned by FakeDocumentRepo.bulk_upsert), not the pre-persist input
    # document's client-generated id.
    input_document = document_repo.bulk_upsert_calls[0][0]
    assert embedded_row.document_id != str(input_document.id)
    assert embedded_row.source_id == "post-1"
    assert embedded_row.checksum == checksum_of("new content")


async def test_embed_batch_partial_skip_only_embeds_changed_item():
    unchanged_text = "same as before"
    checksum = checksum_of(unchanged_text)
    existing_row = EmbeddingRow(
        document_id=str(uuid.uuid4()),
        source_type=EmbeddingSourceType.POST,
        source_id="post-unchanged",
        platform=PlatformName.INSTAGRAM,
        model="fake-model",
        dimensions=3,
        checksum=checksum,
        vector=[0.1, 0.2, 0.3],
    )
    embedding_repo = FakeEmbeddingRepo(
        existing={("post-unchanged", "post", "fake-model"): existing_row}
    )
    provider = FakeProvider()
    document_repo = FakeDocumentRepo()
    service = _make_service(
        provider=provider, document_repo=document_repo, embedding_repo=embedding_repo
    )

    unchanged_item = _make_item(source_id="post-unchanged", text=unchanged_text)
    changed_item = _make_item(source_id="post-changed", text="brand new content")

    count = await service.embed_batch([unchanged_item, changed_item])

    assert count == 1
    # Only the changed item reached the provider and got persisted.
    assert provider.calls == [["brand new content"]]
    persisted_rows = embedding_repo.bulk_upsert_calls[0]
    assert len(persisted_rows) == 1
    assert persisted_rows[0].source_id == "post-changed"


# ============================================================================
# embed_one
# ============================================================================


async def test_embed_one_delegates_to_embed_batch():
    provider = FakeProvider()
    service = _make_service(provider=provider)
    item = _make_item(text="single item")
    count = await service.embed_one(item)
    assert count == 1
    assert provider.calls == [["single item"]]


async def test_embed_one_skips_blank_text():
    service = _make_service()
    item = _make_item(text="")
    assert await service.embed_one(item) == 0
