"""Unit tests for app/config/settings.py and app/database/sql_engine.py."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.config.settings import Settings, get_settings
from app.database.sql_engine import assert_sql_is_safe, validate_sql_tables
from app.models.db.orm import KNOWN_TABLES
from app.utils.exceptions import UnsafeSQLError

# ============================================================================
# Settings
# ============================================================================


@pytest.fixture(autouse=True)
def _clear_settings_cache():
    """get_settings() is lru_cache'd process-wide; clear before/after every
    test so env-var changes in one test never leak into another.
    """
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def test_settings_default_values(monkeypatch):
    for var in (
        "APIFY_API_TOKEN",
        "SUPABASE_URL",
        "SUPABASE_KEY",
        "OPENAI_API_KEY",
        "SUPABASE_DB_URL",
    ):
        monkeypatch.delenv(var, raising=False)
    settings = Settings(_env_file=None)
    assert settings.log_level == "INFO"
    assert settings.log_dir == Path("logs")
    assert settings.openai_chat_model == "gpt-4o-mini"
    assert settings.openai_embedding_model == "text-embedding-3-small"
    assert settings.embedding_dimensions == 1536
    assert settings.max_concurrent_scrapes == 5
    assert settings.apify_instagram_profile_actor == "apify/instagram-profile-scraper"


def test_settings_is_production_property(monkeypatch):
    settings = Settings(_env_file=None, app_env="production")
    assert settings.is_production is True
    settings = Settings(_env_file=None, app_env="development")
    assert settings.is_production is False


def test_settings_log_dir_coerced_from_string():
    settings = Settings(_env_file=None, log_dir="custom_logs")
    assert settings.log_dir == Path("custom_logs")


def test_has_apify_credentials_false_when_unset(monkeypatch):
    monkeypatch.setenv("APIFY_API_TOKEN", "")
    settings = Settings(_env_file=None)
    assert settings.has_apify_credentials is False


def test_has_apify_credentials_true_when_set(monkeypatch):
    monkeypatch.setenv("APIFY_API_TOKEN", "secret-token")
    settings = Settings(_env_file=None)
    assert settings.has_apify_credentials is True


def test_has_supabase_credentials_false_when_missing_either(monkeypatch):
    monkeypatch.setenv("SUPABASE_URL", "https://example.supabase.co")
    monkeypatch.setenv("SUPABASE_KEY", "")
    settings = Settings(_env_file=None)
    assert settings.has_supabase_credentials is False


def test_has_supabase_credentials_true_when_both_set(monkeypatch):
    monkeypatch.setenv("SUPABASE_URL", "https://example.supabase.co")
    monkeypatch.setenv("SUPABASE_KEY", "secret-key")
    settings = Settings(_env_file=None)
    assert settings.has_supabase_credentials is True


def test_has_openai_credentials_false_when_unset(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "")
    settings = Settings(_env_file=None)
    assert settings.has_openai_credentials is False


def test_has_openai_credentials_true_when_set(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-secret")
    settings = Settings(_env_file=None)
    assert settings.has_openai_credentials is True


def test_get_settings_reflects_env_vars_via_monkeypatch(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-from-env")
    get_settings.cache_clear()
    settings = get_settings()
    assert settings.has_openai_credentials is True


def test_get_settings_is_cached_between_calls(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-first")
    get_settings.cache_clear()
    first = get_settings()
    monkeypatch.setenv("OPENAI_API_KEY", "sk-second")
    second = get_settings()
    assert first is second
    assert second.openai_api_key.get_secret_value() == "sk-first"


# ============================================================================
# sql_engine.py
# ============================================================================


def test_assert_sql_is_safe_accepts_valid_select():
    assert_sql_is_safe("SELECT * FROM posts WHERE platform = 'instagram'")


def test_assert_sql_is_safe_accepts_with_prefix_keyword():
    # "with" is an allowed statement-starting keyword.
    sql = "WITH recent AS (SELECT * FROM posts) SELECT * FROM posts"
    assert_sql_is_safe(sql)


def test_assert_sql_is_safe_accepts_reference_to_own_cte_alias():
    # A CTE's own alias, referenced in the outer SELECT (the normal way to
    # use a CTE), must not be rejected just because it isn't a real table —
    # `_CTE_ALIAS_RE` collects `WITH <alias> AS (` names so they're allowed
    # alongside `KNOWN_TABLES`.
    sql = "WITH recent AS (SELECT * FROM posts) SELECT * FROM recent"
    assert_sql_is_safe(sql)


def test_assert_sql_is_safe_accepts_timestamp_columns_named_like_forbidden_keywords():
    # `created_at`/`updated_at`/`deleted_at` contain "create"/"update"/
    # "delete" as substrings; the forbidden-keyword check must match whole
    # words only so these ordinary, ubiquitous columns aren't rejected.
    assert_sql_is_safe("SELECT id, created_at, updated_at FROM posts WHERE deleted_at IS NULL")


def test_assert_sql_is_safe_rejects_insert():
    with pytest.raises(UnsafeSQLError):
        assert_sql_is_safe("INSERT INTO posts (id) VALUES ('x')")


def test_assert_sql_is_safe_rejects_update():
    with pytest.raises(UnsafeSQLError):
        assert_sql_is_safe("UPDATE posts SET caption = 'x'")


def test_assert_sql_is_safe_rejects_delete():
    with pytest.raises(UnsafeSQLError):
        assert_sql_is_safe("DELETE FROM posts")


def test_assert_sql_is_safe_rejects_drop():
    with pytest.raises(UnsafeSQLError):
        assert_sql_is_safe("DROP TABLE posts")


def test_assert_sql_is_safe_rejects_multi_statement_sql():
    with pytest.raises(UnsafeSQLError):
        assert_sql_is_safe("SELECT * FROM posts; DROP TABLE posts;")


def test_assert_sql_is_safe_rejects_non_select_prefix():
    with pytest.raises(UnsafeSQLError):
        assert_sql_is_safe("EXPLAIN SELECT * FROM posts")


def test_assert_sql_is_safe_rejects_unknown_table():
    with pytest.raises(UnsafeSQLError):
        assert_sql_is_safe("SELECT * FROM nonexistent_table")


def test_validate_sql_tables_accepts_known_table():
    assert "posts" in KNOWN_TABLES
    validate_sql_tables("SELECT * FROM posts")


def test_validate_sql_tables_accepts_join_on_known_tables():
    assert "authors" in KNOWN_TABLES
    validate_sql_tables("SELECT * FROM posts JOIN authors ON posts.author_id = authors.id")


def test_validate_sql_tables_rejects_unknown_table():
    assert "nonexistent_table" not in KNOWN_TABLES
    with pytest.raises(UnsafeSQLError):
        validate_sql_tables("SELECT * FROM nonexistent_table")


def test_validate_sql_tables_rejects_unknown_joined_table():
    with pytest.raises(UnsafeSQLError):
        validate_sql_tables("SELECT * FROM posts JOIN nonexistent_table ON true")
