"""Generic repository base class.

Every entity repository is a thin, typed wrapper around one Supabase table.
Centralizing CRUD here means database logic (query building, error mapping,
running the sync postgrest client off the event loop) lives in exactly one
place, and each concrete repository only adds the query methods specific to
its entity.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any

from postgrest.exceptions import APIError
from pydantic import BaseModel

from app.database.supabase_client import get_supabase_client
from app.logging import get_logger
from app.utils.exceptions import DuplicateRecordError, RecordNotFoundError, RepositoryError

logger = get_logger(__name__)


class BaseRepository[ModelT: BaseModel]:
    """CRUD operations for a single Supabase table.

    Subclasses set `table_name` and `model` and inherit create/get/list/
    update/soft_delete/bulk_create. All methods are `async def`: the
    underlying supabase-py client is synchronous, so calls are dispatched
    via `asyncio.to_thread` to keep the ingestion pipeline's concurrency
    (asyncio.gather over many scrape targets) from blocking on network I/O.
    """

    table_name: str
    model: type[ModelT]

    def __init__(self) -> None:
        if not getattr(self, "table_name", None) or not getattr(self, "model", None):
            raise NotImplementedError("Subclasses must set table_name and model")

    @property
    def _table(self) -> Any:
        return get_supabase_client().table(self.table_name)

    def _serialize(self, obj: ModelT) -> dict[str, Any]:
        """Serialize `obj` to a DB-writable payload.

        `model_dump()` includes `@computed_field` properties (e.g.
        `Post.dedup_key`, `Engagement.total_engagement`) by default, but none
        of those are real columns — they're derived for in-process use only
        — so PostgREST rejects the insert with "column ... not found in
        schema cache" if they're left in. Every computed field is dropped
        here rather than at each call site.
        """
        payload = obj.model_dump(mode="json", exclude_none=False)
        for field_name in self.model.model_computed_fields:
            payload.pop(field_name, None)
        return payload

    def _serialize_for_upsert(self, obj: ModelT) -> dict[str, Any]:
        """Serialize for an upsert on a natural-key unique constraint,
        omitting the client-generated `id`.

        Every model generates a fresh UUID client-side (see
        `IdentifiedMixin`), so a naive upsert would try to overwrite the
        existing row's `id` on every re-ingestion of the same natural key —
        which Postgres rejects once any child row (comments.author_id,
        media.post_id, ...) has a foreign key pointing at that id. Dropping
        `id` from the payload lets Postgres keep the existing primary key on
        UPDATE and fall back to the column default on INSERT.
        """
        payload = self._serialize(obj)
        payload.pop("id", None)
        return payload

    def _deserialize(self, row: dict[str, Any]) -> ModelT:
        return self.model.model_validate(row)

    async def get_by_id(self, record_id: str) -> ModelT | None:
        def _run() -> Any:
            return self._table.select("*").eq("id", record_id).limit(1).execute()

        response = await asyncio.to_thread(_run)
        rows = response.data
        return self._deserialize(rows[0]) if rows else None

    async def require_by_id(self, record_id: str) -> ModelT:
        record = await self.get_by_id(record_id)
        if record is None:
            raise RecordNotFoundError(
                f"{self.model.__name__} {record_id} not found", context={"id": record_id}
            )
        return record

    async def list_all(
        self,
        *,
        filters: dict[str, Any] | None = None,
        limit: int = 100,
        offset: int = 0,
        order_by: str | None = None,
        descending: bool = True,
        include_deleted: bool = False,
    ) -> list[ModelT]:
        def _run() -> Any:
            query = self._table.select("*")
            if not include_deleted and "deleted_at" in self.model.model_fields:
                query = query.is_("deleted_at", "null")
            for key, value in (filters or {}).items():
                query = query.eq(key, value)
            if order_by:
                query = query.order(order_by, desc=descending)
            return query.range(offset, offset + limit - 1).execute()

        response = await asyncio.to_thread(_run)
        return [self._deserialize(row) for row in response.data]

    async def create(self, obj: ModelT) -> ModelT:
        payload = self._serialize(obj)

        def _run() -> Any:
            return self._table.insert(payload).execute()

        try:
            response = await asyncio.to_thread(_run)
        except APIError as exc:
            if exc.code == "23505":  # unique_violation
                raise DuplicateRecordError(str(exc), context={"payload": payload}) from exc
            raise RepositoryError(str(exc), context={"payload": payload}) from exc
        logger.debug("Database insert", table=self.table_name, id=response.data[0].get("id"))
        return self._deserialize(response.data[0])

    async def upsert(self, obj: ModelT, *, on_conflict: str) -> ModelT:
        """Insert or update on the given unique constraint columns —
        the primary mechanism the ingestion pipeline uses for deduplication.
        """
        payload = self._serialize_for_upsert(obj)

        def _run() -> Any:
            return self._table.upsert(payload, on_conflict=on_conflict).execute()

        try:
            response = await asyncio.to_thread(_run)
        except APIError as exc:
            raise RepositoryError(str(exc), context={"payload": payload}) from exc
        logger.debug("Database upsert", table=self.table_name, on_conflict=on_conflict, count=1)
        return self._deserialize(response.data[0])

    async def bulk_upsert(self, objs: list[ModelT], *, on_conflict: str) -> list[ModelT]:
        """Batch upsert — used by the ingestion pipeline so a scrape of
        thousands of posts is a handful of round trips, not one-per-row.
        """
        if not objs:
            return []
        payloads = [self._serialize_for_upsert(o) for o in objs]

        def _run() -> Any:
            return self._table.upsert(payloads, on_conflict=on_conflict).execute()

        try:
            response = await asyncio.to_thread(_run)
        except APIError as exc:
            raise RepositoryError(str(exc), context={"count": len(payloads)}) from exc
        logger.debug(
            "Database bulk upsert",
            table=self.table_name,
            on_conflict=on_conflict,
            count=len(payloads),
        )
        return [self._deserialize(row) for row in response.data]

    async def update(self, record_id: str, data: dict[str, Any]) -> ModelT:
        def _run() -> Any:
            return self._table.update(data).eq("id", record_id).execute()

        try:
            response = await asyncio.to_thread(_run)
        except APIError as exc:
            raise RepositoryError(str(exc), context={"id": record_id}) from exc
        if not response.data:
            raise RecordNotFoundError(f"{self.model.__name__} {record_id} not found")
        logger.debug("Database update", table=self.table_name, id=record_id)
        return self._deserialize(response.data[0])

    async def soft_delete(self, record_id: str) -> None:
        await self.update(record_id, {"deleted_at": datetime.now(UTC).isoformat()})

    async def count(self, filters: dict[str, Any] | None = None) -> int:
        def _run() -> Any:
            query = self._table.select("id", count="exact")
            for key, value in (filters or {}).items():
                query = query.eq(key, value)
            return query.execute()

        response = await asyncio.to_thread(_run)
        return response.count or 0
