"""Unit tests for app/utils/{text,retry,exceptions}.py."""

from __future__ import annotations

import pytest

from app.utils.exceptions import (
    ApifyRateLimitError,
    ApifyRunFailedError,
    AppError,
    AssistantError,
    DatabaseConnectionError,
    DuplicateRecordError,
    EmbeddingError,
    NormalizationError,
    RecordNotFoundError,
    RepositoryError,
    RetrievalError,
    ScraperError,
    SQLGenerationError,
    UnsafeSQLError,
    UnsupportedPlatformError,
    ValidationFailedError,
)
from app.utils.retry import with_retry
from app.utils.text import extract_hashtags, extract_mentions, extract_urls, guess_language

# ============================================================================
# text.py
# ============================================================================


def test_extract_hashtags_basic():
    assert extract_hashtags("loving this #sunset and #beach") == ["sunset", "beach"]


def test_extract_hashtags_lowercases_and_dedupes():
    assert extract_hashtags("#Sunset #sunset #SUNSET") == ["sunset"]


def test_extract_hashtags_none_input():
    assert extract_hashtags(None) == []


def test_extract_hashtags_empty_string():
    assert extract_hashtags("") == []


def test_extract_hashtags_no_hashtags():
    assert extract_hashtags("just plain text") == []


def test_extract_hashtags_ignores_mid_word_hash():
    assert extract_hashtags("price is $5#not_a_tag") == []


def test_extract_mentions_basic():
    assert extract_mentions("hello @alice and @bob") == ["alice", "bob"]


def test_extract_mentions_lowercases_and_dedupes():
    assert extract_mentions("@Alice @alice @ALICE") == ["alice"]


def test_extract_mentions_none_input():
    assert extract_mentions(None) == []


def test_extract_mentions_no_mentions():
    assert extract_mentions("no mentions here") == []


def test_extract_urls_basic():
    text = "check https://example.com/page and http://foo.org"
    assert extract_urls(text) == ["https://example.com/page", "http://foo.org"]


def test_extract_urls_strips_trailing_punctuation():
    text = "see (https://example.com/page)."
    assert extract_urls(text) == ["https://example.com/page"]


def test_extract_urls_dedupes():
    text = "https://example.com https://example.com"
    assert extract_urls(text) == ["https://example.com"]


def test_extract_urls_none_input():
    assert extract_urls(None) == []


def test_extract_urls_no_urls():
    assert extract_urls("nothing here") == []


def test_guess_language_ascii_text_is_english():
    assert guess_language("hello world") == "en"


def test_guess_language_non_ascii_is_undetermined():
    assert guess_language("こんにちは") == "und"


def test_guess_language_none_input_is_undetermined():
    assert guess_language(None) == "und"


def test_guess_language_blank_string_is_undetermined():
    assert guess_language("   ") == "und"


# ============================================================================
# retry.py
# ============================================================================


class _FlakyError(Exception):
    pass


class _OtherError(Exception):
    pass


async def test_with_retry_succeeds_after_transient_failures():
    attempts = {"count": 0}

    @with_retry(exceptions=(_FlakyError,), max_attempts=3, min_wait=0.01, max_wait=0.02)
    async def flaky() -> str:
        attempts["count"] += 1
        if attempts["count"] < 3:
            raise _FlakyError("not yet")
        return "ok"

    result = await flaky()
    assert result == "ok"
    assert attempts["count"] == 3


async def test_with_retry_reraises_after_max_attempts_exhausted():
    attempts = {"count": 0}

    @with_retry(exceptions=(_FlakyError,), max_attempts=3, min_wait=0.01, max_wait=0.02)
    async def always_fails() -> None:
        attempts["count"] += 1
        raise _FlakyError("still failing")

    with pytest.raises(_FlakyError):
        await always_fails()
    assert attempts["count"] == 3


async def test_with_retry_does_not_retry_unlisted_exception_type():
    attempts = {"count": 0}

    @with_retry(exceptions=(_FlakyError,), max_attempts=3, min_wait=0.01, max_wait=0.02)
    async def raises_other() -> None:
        attempts["count"] += 1
        raise _OtherError("not retryable")

    with pytest.raises(_OtherError):
        await raises_other()
    assert attempts["count"] == 1


# ============================================================================
# exceptions.py
# ============================================================================


def test_app_error_stores_message_and_context():
    err = AppError("something failed", context={"key": "value"})
    assert err.message == "something failed"
    assert err.context == {"key": "value"}
    assert str(err) == "something failed"


def test_app_error_default_context_is_empty_dict():
    err = AppError("failure")
    assert err.context == {}


@pytest.mark.parametrize(
    ("exc_cls", "base_cls"),
    [
        (ScraperError, AppError),
        (ApifyRunFailedError, ScraperError),
        (ApifyRateLimitError, ScraperError),
        (UnsupportedPlatformError, ScraperError),
        (ValidationFailedError, AppError),
        (NormalizationError, AppError),
        (RepositoryError, AppError),
        (RecordNotFoundError, RepositoryError),
        (DuplicateRecordError, RepositoryError),
        (DatabaseConnectionError, RepositoryError),
        (EmbeddingError, AppError),
        (RetrievalError, AppError),
        (AssistantError, AppError),
        (SQLGenerationError, AssistantError),
        (UnsafeSQLError, SQLGenerationError),
    ],
)
def test_exception_hierarchy(exc_cls, base_cls):
    assert issubclass(exc_cls, base_cls)
    assert issubclass(exc_cls, AppError)


def test_duplicate_record_error_is_repository_error_is_app_error():
    err = DuplicateRecordError("dup", context={"table": "posts"})
    assert isinstance(err, RepositoryError)
    assert isinstance(err, AppError)
    assert err.context == {"table": "posts"}


def test_unsafe_sql_error_context_preserved_through_hierarchy():
    err = UnsafeSQLError("bad sql", context={"sql": "DROP TABLE x"})
    assert isinstance(err, SQLGenerationError)
    assert isinstance(err, AssistantError)
    assert isinstance(err, AppError)
    assert err.context == {"sql": "DROP TABLE x"}


def test_can_catch_specific_error_as_app_error():
    with pytest.raises(AppError):
        raise RecordNotFoundError("missing", context={"id": "123"})
