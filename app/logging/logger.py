"""Structured logging setup built on Loguru + Rich.

A single `configure_logging()` call wires up:
- a Rich-formatted console sink for human-readable local development
- a rotating JSON file sink per app_env for machine-parseable production logs
- automatic masking of sensitive keys (tokens, api keys, secrets)

Every other module should just `from app.logging import get_logger` and use
the returned bound logger; nothing else in the codebase should touch
`logging` or `loguru` directly.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from loguru import logger as _logger
from rich.console import Console
from rich.logging import RichHandler

from app.config import get_settings

if TYPE_CHECKING:
    from loguru import Record

_SENSITIVE_KEYS = {"token", "api_key", "apikey", "password", "secret", "authorization", "key"}
_CONFIGURED = False


def _mask_sensitive(record: Record) -> bool:
    """Loguru filter that redacts sensitive values found in `extra`."""
    extra = record.get("extra", {})
    for k in list(extra.keys()):
        if any(sensitive in k.lower() for sensitive in _SENSITIVE_KEYS):
            extra[k] = "***REDACTED***"
    return True


def configure_logging() -> None:
    """Idempotently configure Loguru sinks. Safe to call multiple times."""
    global _CONFIGURED
    if _CONFIGURED:
        return

    settings = get_settings()
    _logger.remove()

    console = Console(stderr=True)
    _logger.add(
        RichHandler(console=console, rich_tracebacks=True, markup=True),
        level=settings.log_level,
        format="{message}",
        filter=_mask_sensitive,
    )

    settings.log_dir.mkdir(parents=True, exist_ok=True)
    _logger.add(
        settings.log_dir / "app.jsonl",
        level=settings.log_level,
        serialize=True,
        rotation="10 MB",
        retention="14 days",
        enqueue=True,
        filter=_mask_sensitive,
    )
    _logger.add(
        settings.log_dir / "errors.jsonl",
        level="ERROR",
        serialize=True,
        rotation="10 MB",
        retention="30 days",
        enqueue=True,
        filter=_mask_sensitive,
    )

    _CONFIGURED = True


def get_logger(name: str) -> Any:
    """Return a Loguru logger bound to `name` (typically `__name__`)."""
    configure_logging()
    return _logger.bind(component=name)
