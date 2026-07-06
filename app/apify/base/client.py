"""Thin async wrapper around `apify_client.ApifyClient`.

`apify-client` is a synchronous SDK; every call here is dispatched through
`asyncio.to_thread` so the ingestion pipeline's concurrency (asyncio.gather
over many scrape targets, bounded by `settings.max_concurrent_scrapes`)
never blocks on network I/O. Centralizing the "run an actor, wait for it,
fetch the dataset" sequence here means every platform scraper shares
identical retry/rate-limit/failure handling instead of reimplementing it.
"""

from __future__ import annotations

import asyncio
from datetime import timedelta
from functools import lru_cache
from typing import Any

from apify_client import ApifyClient
from apify_client._models import Run

from app.config import get_settings
from app.logging import get_logger
from app.utils.exceptions import ApifyRateLimitError, ApifyRunFailedError
from app.utils.retry import with_retry

logger = get_logger(__name__)

# Apify run statuses that mean the run is done but did not succeed.
_FAILURE_STATUSES = {"FAILED", "ABORTED", "TIMED-OUT"}


@lru_cache
def get_apify_client() -> ApifyClient:
    """Return a process-wide cached Apify client."""
    settings = get_settings()
    return ApifyClient(settings.apify_api_token.get_secret_value())


class ApifyActorRunner:
    """Runs an Apify actor to completion and returns its dataset items.

    Kept as a small class (rather than free functions) so it can hold a
    client reference and be swapped for a fake in tests without patching
    module-level globals.
    """

    def __init__(self, client: ApifyClient | None = None) -> None:
        self._client = client or get_apify_client()

    @with_retry(exceptions=(ApifyRateLimitError,), max_attempts=4, min_wait=2.0, max_wait=30.0)
    async def run_and_fetch(
        self,
        actor_id: str,
        run_input: dict[str, Any],
        *,
        memory_mbytes: int | None = None,
        timeout_secs: int | None = None,
    ) -> list[dict[str, Any]]:
        """Start `actor_id` with `run_input`, block until it finishes, and
        return every item from its default dataset.

        Raises `ApifyRunFailedError` if the run finishes in a failure state,
        `ApifyRateLimitError` on HTTP 429 (retried automatically).
        """

        def _run() -> Run | None:
            try:
                return self._client.actor(actor_id).call(
                    run_input=run_input,
                    memory_mbytes=memory_mbytes,
                    run_timeout=timedelta(seconds=timeout_secs) if timeout_secs else None,
                )
            except Exception as exc:  # apify_client raises ApifyApiError
                status_code = getattr(exc, "status_code", None)
                if status_code == 429:
                    raise ApifyRateLimitError(
                        f"Rate limited running actor {actor_id}", context={"actor_id": actor_id}
                    ) from exc
                raise ApifyRunFailedError(
                    f"Actor {actor_id} run failed to start: {exc}",
                    context={"actor_id": actor_id},
                ) from exc

        logger.info("Starting Apify actor run", actor_id=actor_id, run_input=run_input)
        run = await asyncio.to_thread(_run)
        if run is None:
            raise ApifyRunFailedError(
                f"Actor {actor_id} run returned no result", context={"actor_id": actor_id}
            )

        if run.status in _FAILURE_STATUSES:
            raise ApifyRunFailedError(
                f"Actor {actor_id} run finished with status {run.status}",
                context={"actor_id": actor_id, "run_id": run.id, "status": run.status},
            )

        dataset_id = run.default_dataset_id

        def _fetch_items() -> list[dict[str, Any]]:
            return list(self._client.dataset(dataset_id).iterate_items())

        items = await asyncio.to_thread(_fetch_items)
        logger.info(
            "Apify actor run finished",
            actor_id=actor_id,
            run_id=run.id,
            item_count=len(items),
        )
        return items
