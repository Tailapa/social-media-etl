"""Unit tests for app/repositories/base.py's BaseRepository generic CRUD logic.

The single most important behavior under test is `_serialize_for_upsert`
dropping the client-generated `id` before an upsert — a deliberate fix for a
real bug where re-ingesting an existing natural-key row would try to
overwrite its primary key and violate FK constraints from child rows (see the
docstring on that method). Every test here works against fakes; nothing
touches a real Supabase connection.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from postgrest.exceptions import APIError

from app.repositories.author_repository import AuthorRepository
from app.repositories.post_repository import PostRepository
from app.utils.exceptions import DuplicateRecordError, RecordNotFoundError, RepositoryError

# --- _serialize / _serialize_for_upsert (no DB involved) --------------------


def test_serialize_includes_id(make_author):
    repo = AuthorRepository()
    author = make_author()
    payload = repo._serialize(author)
    assert "id" in payload
    assert payload["id"] == str(author.id)


def test_serialize_for_upsert_drops_id(make_author):
    repo = AuthorRepository()
    author = make_author()
    payload = repo._serialize_for_upsert(author)
    assert "id" not in payload


def test_serialize_for_upsert_keeps_other_fields(make_author):
    repo = AuthorRepository()
    author = make_author(username="someone")
    payload = repo._serialize_for_upsert(author)
    assert payload["username"] == "someone"
    assert payload["platform_user_id"] == author.platform_user_id


def test_serialize_for_upsert_drops_id_for_post_repo(make_post):
    repo = PostRepository()
    post = make_post(author_id="author-1")
    upsert_payload = repo._serialize_for_upsert(post)
    create_payload = repo._serialize(post)
    assert "id" not in upsert_payload
    assert "id" in create_payload
    assert create_payload["id"] == str(post.id)


# --- Fake Supabase client / table builder ------------------------------------


class FakeResponse:
    def __init__(self, data: list[dict[str, Any]], count: int | None = None) -> None:
        self.data = data
        self.count = count


class FakeTableBuilder:
    """Records every call made against it and returns canned responses.

    Mimics just enough of postgrest's fluent query-builder interface
    (chainable `.eq/.select/.order/.range/...` all returning `self`) for
    `BaseRepository` methods to run end to end without a real connection.
    """

    def __init__(
        self, table_name: str, *, responses: dict[str, FakeResponse] | None = None
    ) -> None:
        self.table_name = table_name
        self.calls: list[tuple[str, tuple, dict]] = []
        self._responses = responses or {}
        self._next_response = FakeResponse([])

    def _record(self, name: str, *args: Any, **kwargs: Any) -> FakeTableBuilder:
        self.calls.append((name, args, kwargs))
        return self

    def select(self, *args, **kwargs):
        return self._record("select", *args, **kwargs)

    def insert(self, payload, **kwargs):
        self._record("insert", payload, **kwargs)
        if "insert" in self._responses:
            self._next_response = self._responses["insert"]
        else:
            rows = payload if isinstance(payload, list) else [payload]
            self._next_response = FakeResponse(
                [{**row, "id": row.get("id") or str(uuid.uuid4())} for row in rows]
            )
        return self

    def upsert(self, payload, **kwargs):
        self._record("upsert", payload, **kwargs)
        rows = payload if isinstance(payload, list) else [payload]
        self._next_response = self._responses.get(
            "upsert",
            FakeResponse([{**row, "id": row.get("id") or str(uuid.uuid4())} for row in rows]),
        )
        return self

    def update(self, data, **kwargs):
        self._record("update", data, **kwargs)
        self._next_response = self._responses.get("update", FakeResponse([]))
        return self

    def eq(self, *args, **kwargs):
        return self._record("eq", *args, **kwargs)

    def is_(self, *args, **kwargs):
        return self._record("is_", *args, **kwargs)

    def order(self, *args, **kwargs):
        return self._record("order", *args, **kwargs)

    def range(self, *args, **kwargs):
        return self._record("range", *args, **kwargs)

    def limit(self, *args, **kwargs):
        return self._record("limit", *args, **kwargs)

    def gte(self, *args, **kwargs):
        return self._record("gte", *args, **kwargs)

    def lte(self, *args, **kwargs):
        return self._record("lte", *args, **kwargs)

    def ilike(self, *args, **kwargs):
        return self._record("ilike", *args, **kwargs)

    def execute(self):
        return self._next_response


class FakeSupabaseClient:
    def __init__(self) -> None:
        self.tables: dict[str, FakeTableBuilder] = {}

    def table(self, name: str) -> FakeTableBuilder:
        builder = self.tables.setdefault(name, FakeTableBuilder(name))
        return builder


@pytest.fixture
def fake_client(monkeypatch) -> FakeSupabaseClient:
    client = FakeSupabaseClient()
    monkeypatch.setattr("app.repositories.base.get_supabase_client", lambda: client)
    return client


# --- create() includes id, upsert()/bulk_upsert() drop it --------------------


async def test_create_sends_payload_with_id(fake_client, make_author):
    repo = AuthorRepository()
    author = make_author()
    await repo.create(author)
    table = fake_client.tables["authors"]
    insert_calls = [c for c in table.calls if c[0] == "insert"]
    assert len(insert_calls) == 1
    payload = insert_calls[0][1][0]
    assert "id" in payload
    assert payload["id"] == str(author.id)


async def test_upsert_sends_payload_without_id(fake_client, make_author):
    repo = AuthorRepository()
    author = make_author()
    await repo.upsert_author(author)
    table = fake_client.tables["authors"]
    upsert_calls = [c for c in table.calls if c[0] == "upsert"]
    assert len(upsert_calls) == 1
    payload, kwargs = upsert_calls[0][1][0], upsert_calls[0][2]
    assert "id" not in payload
    assert kwargs["on_conflict"] == "platform,platform_user_id"


async def test_bulk_upsert_sends_payloads_without_id(fake_client, make_author):
    repo = AuthorRepository()
    authors = [make_author(username="a"), make_author(username="b")]
    await repo.bulk_upsert_authors(authors)
    table = fake_client.tables["authors"]
    upsert_calls = [c for c in table.calls if c[0] == "upsert"]
    assert len(upsert_calls) == 1
    payloads = upsert_calls[0][1][0]
    assert len(payloads) == 2
    assert all("id" not in p for p in payloads)


async def test_bulk_upsert_empty_list_short_circuits(fake_client):
    repo = AuthorRepository()
    result = await repo.bulk_upsert_authors([])
    assert result == []
    assert "authors" not in fake_client.tables


async def test_upsert_post_drops_id_too(fake_client, make_post):
    repo = PostRepository()
    post = make_post(author_id="author-1")
    await repo.upsert_post(post)
    table = fake_client.tables["posts"]
    upsert_calls = [c for c in table.calls if c[0] == "upsert"]
    payload = upsert_calls[0][1][0]
    assert "id" not in payload
    assert payload["author_id"] == "author-1"


# --- other generic CRUD behavior ---------------------------------------------


async def test_get_by_id_found(fake_client, make_author):
    author = make_author()
    # select() doesn't stash a canned response the way insert/upsert do above;
    # patch execute() directly for the select path.
    table = fake_client.tables["authors"] = FakeTableBuilder("authors")
    table._next_response = FakeResponse([author.model_dump(mode="json")])
    repo = AuthorRepository()
    found = await repo.get_by_id(str(author.id))
    assert found is not None
    assert found.id == author.id


async def test_get_by_id_not_found(fake_client):
    repo = AuthorRepository()
    found = await repo.get_by_id("00000000-0000-0000-0000-000000000000")
    assert found is None


async def test_create_maps_unique_violation_to_duplicate_error(fake_client, make_author):
    class RaisingTable(FakeTableBuilder):
        def execute(self):
            raise APIError(
                {"message": "duplicate key", "code": "23505", "details": None, "hint": None}
            )

    fake_client.tables["authors"] = RaisingTable("authors")
    repo = AuthorRepository()
    with pytest.raises(DuplicateRecordError):
        await repo.create(make_author())


async def test_create_maps_other_api_error_to_repository_error(fake_client, make_author):
    class RaisingTable(FakeTableBuilder):
        def execute(self):
            raise APIError({"message": "boom", "code": "99999", "details": None, "hint": None})

    fake_client.tables["authors"] = RaisingTable("authors")
    repo = AuthorRepository()
    with pytest.raises(RepositoryError):
        await repo.create(make_author())


async def test_update_raises_record_not_found_when_no_rows_returned(fake_client):
    repo = AuthorRepository()
    with pytest.raises(RecordNotFoundError):
        await repo.update("some-id", {"username": "new"})


async def test_soft_delete_calls_update_with_deleted_at(fake_client, make_author):
    author = make_author()
    fake_client.tables["authors"] = FakeTableBuilder(
        "authors", responses={"update": FakeResponse([author.model_dump(mode="json")])}
    )
    repo = AuthorRepository()
    await repo.soft_delete(str(author.id))
    table = fake_client.tables["authors"]
    update_calls = [c for c in table.calls if c[0] == "update"]
    assert len(update_calls) == 1
    assert "deleted_at" in update_calls[0][1][0]


async def test_count_returns_response_count(fake_client):
    table = fake_client.tables["authors"] = FakeTableBuilder("authors")
    table._next_response = FakeResponse([], count=42)
    repo = AuthorRepository()
    assert await repo.count() == 42


async def test_count_defaults_to_zero_when_none(fake_client):
    repo = AuthorRepository()
    assert await repo.count() == 0
