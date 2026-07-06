"""Shared normalization helpers: deduplication and merge-on-conflict logic
used by every platform normalizer and by the ingestion pipeline.

Kept platform-agnostic so `app/ingestion/pipeline.py` can dedupe a mixed
batch (e.g. posts scraped twice across two runs) without knowing which
platform produced them.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from typing import Any


def dedupe_by_key[T](items: Iterable[T], key_fn: Callable[[T], str]) -> list[T]:
    """Collapse `items` to one-per-key, keeping the *last* occurrence.

    "Last wins" because ingestion processes items in scrape order and a
    later page of results (e.g. an updated comment count) should win over
    an earlier one within the same batch.
    """
    seen: dict[str, T] = {}
    for item in items:
        seen[key_fn(item)] = item
    return list(seen.values())


def get_or_register[T](cache: dict[str, T], item: T, key_fn: Callable[[T], str]) -> T:
    """Return the canonical (first-seen) item for `key_fn(item)`, registering
    it in `cache` on first sight.

    Scrapers normalize one `Author` per raw item (a post/comment/video each
    carries its own embedded owner info), so the same real-world author gets
    a *fresh* object — and a fresh client-generated `.id` — on every
    occurrence. Stamping a post's `author_id` from that per-item object
    before deduping, then deduping the author list afterward with
    `dedupe_by_key`, silently discards every id but the last-seen one,
    orphaning any post that referenced an earlier occurrence (a real FK
    violation once persisted). Using this helper instead — building the
    cache *while* iterating, and reading `.id` off the cached (canonical)
    object — keeps every reference consistent from the start, so no
    separate dedupe pass is needed afterward.
    """
    key = key_fn(item)
    if key not in cache:
        cache[key] = item
    return cache[key]


def merge_prefer_non_null(existing: dict[str, Any], incoming: dict[str, Any]) -> dict[str, Any]:
    """Field-by-field merge: `incoming` wins unless its value is None/empty
    and `existing` has a real value. Used to reconcile a duplicate author
    scraped in two different runs (e.g. bio populated in one, missing in
    the other) without ever regressing a previously-known field to null.
    """
    merged = dict(existing)
    for key, value in incoming.items():
        if value is None or value == "" or value == [] or value == {}:
            continue
        merged[key] = value
    return merged


def first_present(source: dict[str, Any], *keys: str, default: Any = None) -> Any:
    """Return the first non-None value found in `source` across `keys`.

    Apify actors are not internally consistent about field naming across
    actor versions (e.g. `commentsCount` vs `comments_count` vs
    `commentCount`); normalizers use this instead of a long if/elif chain.
    """
    for key in keys:
        if key in source and source[key] is not None:
            return source[key]
    return default


def as_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def as_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
