"""EmbeddingDocument: a chunk of text plus its vector, linked back to the
source record it was derived from. This is the unit stored in the
`embeddings` table and returned by semantic search.
"""

from __future__ import annotations

from pydantic import Field, model_validator

from app.models.pydantic.base import BaseSchema, IdentifiedMixin, TimestampMixin
from app.models.pydantic.enums import EmbeddingSourceType, PlatformName


class EmbeddingDocument(IdentifiedMixin, TimestampMixin, BaseSchema):
    source_type: EmbeddingSourceType
    source_id: str = Field(..., description="ID of the Post/Comment/Video this was derived from")
    platform: PlatformName
    text: str
    vector: list[float] = Field(default_factory=list, repr=False)
    model: str
    dimensions: int
    checksum: str = Field(..., description="sha256 of `text`, used to avoid re-embedding")
    metadata: dict = Field(default_factory=dict)

    @model_validator(mode="after")
    def _validate_vector_length(self) -> EmbeddingDocument:
        if self.vector and len(self.vector) != self.dimensions:
            raise ValueError(f"vector has {len(self.vector)} dims but dimensions={self.dimensions}")
        return self
