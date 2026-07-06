"""Shared retry policies built on Tenacity.

Centralizing retry configuration means every outbound call (Apify, Supabase,
OpenAI) backs off and logs the same way, instead of each module reinventing
its own loop.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, TypeVar

from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential_jitter,
)

from app.logging import get_logger

logger = get_logger(__name__)

T = TypeVar("T")


def _log_retry(retry_state: Any) -> None:
    logger.warning(
        "Retrying after failure",
        attempt=retry_state.attempt_number,
        wait=str(retry_state.next_action.sleep) if retry_state.next_action else None,
        exception=str(retry_state.outcome.exception()) if retry_state.outcome else None,
    )


def with_retry(
    *,
    exceptions: tuple[type[BaseException], ...] = (Exception,),
    max_attempts: int = 3,
    min_wait: float = 1.0,
    max_wait: float = 20.0,
) -> Callable[[Callable[..., T]], Callable[..., T]]:
    """Return a Tenacity decorator with a standard exponential-backoff policy."""
    return retry(
        reraise=True,
        stop=stop_after_attempt(max_attempts),
        wait=wait_exponential_jitter(initial=min_wait, max=max_wait),
        retry=retry_if_exception_type(exceptions),
        before_sleep=_log_retry,
    )
