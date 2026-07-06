"""Centralized application configuration.

All environment-driven configuration lives here as a single Pydantic
Settings object so every other module has one place to source secrets
and tunables from, instead of reading `os.environ` ad-hoc.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application-wide settings loaded from environment / .env file."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # --- App ---
    app_env: Literal["development", "staging", "production", "test"] = "development"
    log_level: str = "INFO"
    log_dir: Path = Path("logs")

    # --- Apify ---
    apify_api_token: SecretStr = Field(default=SecretStr(""))
    apify_instagram_profile_actor: str = "apify/instagram-profile-scraper"
    apify_instagram_post_actor: str = "apify/instagram-post-scraper"
    apify_instagram_hashtag_actor: str = "apify/instagram-hashtag-scraper"
    apify_instagram_comment_actor: str = "apify/instagram-comment-scraper"
    apify_twitter_scraper_actor: str = "apidojo/tweet-scraper"
    apify_youtube_scraper_actor: str = "streamers/youtube-scraper"
    apify_youtube_comment_actor: str = "streamers/youtube-comments-scraper"
    apify_youtube_transcript_actor: str = "pintostudio/youtube-transcript-scraper"

    # --- Supabase ---
    supabase_url: str = ""
    supabase_key: SecretStr = Field(default=SecretStr(""))
    supabase_db_url: str = ""

    # --- OpenAI ---
    openai_api_key: SecretStr = Field(default=SecretStr(""))
    openai_chat_model: str = "gpt-4o-mini"
    openai_embedding_model: str = "text-embedding-3-small"

    # --- Retrieval / performance ---
    embedding_dimensions: int = 1536
    max_concurrent_scrapes: int = 5

    @field_validator("log_dir", mode="before")
    @classmethod
    def _coerce_path(cls, value: str | Path) -> Path:
        return Path(value)

    @property
    def is_production(self) -> bool:
        return self.app_env == "production"

    @property
    def has_apify_credentials(self) -> bool:
        return bool(self.apify_api_token.get_secret_value())

    @property
    def has_supabase_credentials(self) -> bool:
        return bool(self.supabase_url and self.supabase_key.get_secret_value())

    @property
    def has_openai_credentials(self) -> bool:
        return bool(self.openai_api_key.get_secret_value())


@lru_cache
def get_settings() -> Settings:
    """Return a cached, process-wide Settings instance."""
    return Settings()
