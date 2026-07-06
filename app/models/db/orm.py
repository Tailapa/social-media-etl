"""SQLAlchemy Core table metadata mirroring migrations/*.sql.

This is *not* used as a second persistence path — repositories talk to
Supabase exclusively via PostgREST (see app/repositories). This metadata
object exists so the AI SQL generator can validate that generated SQL only
references real tables/columns (app/ai/sql_generator.py) without needing a
live database round-trip, and so `scripts/print_schema.py` has a single
source of truth to render docs from.

Every table created in migrations/ must have an entry here — `KNOWN_TABLES`
is the whitelist `assert_sql_is_safe` uses to reject hallucinated table
references in AI-generated SQL.
"""

from __future__ import annotations

from sqlalchemy import (
    ARRAY,
    BigInteger,
    Boolean,
    Column,
    ForeignKey,
    Integer,
    MetaData,
    Numeric,
    Table,
    Text,
)
from sqlalchemy.dialects.postgresql import JSONB, TIMESTAMP, UUID

metadata = MetaData()

platforms = Table(
    "platforms",
    metadata,
    Column("id", UUID, primary_key=True),
    Column("name", Text, unique=True, nullable=False),
    Column("display_name", Text, nullable=False),
    Column("is_active", Boolean),
    Column("created_at", TIMESTAMP),
    Column("updated_at", TIMESTAMP),
)

authors = Table(
    "authors",
    metadata,
    Column("id", UUID, primary_key=True),
    Column("platform", Text, ForeignKey("platforms.name"), nullable=False),
    Column("platform_user_id", Text, nullable=False),
    Column("username", Text, nullable=False),
    Column("display_name", Text),
    Column("bio", Text),
    Column("is_verified", Boolean),
    Column("is_private", Boolean),
    Column("follower_count", BigInteger),
    Column("following_count", BigInteger),
    Column("post_count", BigInteger),
    Column("platform_metadata", JSONB),
    Column("created_at", TIMESTAMP),
    Column("updated_at", TIMESTAMP),
    Column("deleted_at", TIMESTAMP),
)

channels = Table(
    "channels",
    metadata,
    Column("id", UUID, primary_key=True),
    Column("platform", Text, ForeignKey("platforms.name"), nullable=False),
    Column("platform_channel_id", Text, nullable=False),
    Column("author_id", UUID, ForeignKey("authors.id"), nullable=False),
    Column("name", Text, nullable=False),
    Column("subscriber_count", BigInteger),
    Column("video_count", BigInteger),
    Column("total_views", BigInteger),
    Column("platform_metadata", JSONB),
    Column("created_at", TIMESTAMP),
    Column("updated_at", TIMESTAMP),
    Column("deleted_at", TIMESTAMP),
)

posts = Table(
    "posts",
    metadata,
    Column("id", UUID, primary_key=True),
    Column("platform", Text, ForeignKey("platforms.name"), nullable=False),
    Column("platform_post_id", Text, nullable=False),
    Column("author_id", UUID, ForeignKey("authors.id"), nullable=False),
    Column("content_type", Text, nullable=False),
    Column("caption", Text),
    Column("content", Text),
    Column("language", Text),
    Column("url", Text),
    Column("hashtags", ARRAY(Text)),
    Column("mentions", ARRAY(Text)),
    Column("urls", ARRAY(Text)),
    Column("posted_at", TIMESTAMP),
    Column("is_pinned", Boolean),
    Column("is_sponsored", Boolean),
    Column("platform_metadata", JSONB),
    Column("created_at", TIMESTAMP),
    Column("updated_at", TIMESTAMP),
    Column("deleted_at", TIMESTAMP),
)

videos = Table(
    "videos",
    metadata,
    Column("id", UUID, primary_key=True),
    Column("platform", Text, ForeignKey("platforms.name"), nullable=False),
    Column("platform_video_id", Text, nullable=False),
    Column("channel_id", UUID, ForeignKey("channels.id"), nullable=False),
    Column("post_id", UUID, ForeignKey("posts.id")),
    Column("title", Text, nullable=False),
    Column("description", Text),
    Column("transcript", Text),
    Column("duration_seconds", Numeric),
    Column("published_at", TIMESTAMP),
    Column("created_at", TIMESTAMP),
    Column("updated_at", TIMESTAMP),
    Column("deleted_at", TIMESTAMP),
)

media = Table(
    "media",
    metadata,
    Column("id", UUID, primary_key=True),
    Column("post_id", UUID, ForeignKey("posts.id")),
    Column("media_type", Text, nullable=False),
    Column("url", Text, nullable=False),
    Column("width", Integer),
    Column("height", Integer),
    Column("order_index", Integer),
    Column("created_at", TIMESTAMP),
)

hashtags = Table(
    "hashtags",
    metadata,
    Column("id", UUID, primary_key=True),
    Column("tag", Text, unique=True, nullable=False),
    Column("created_at", TIMESTAMP),
)

post_hashtags = Table(
    "post_hashtags",
    metadata,
    Column("post_id", UUID, ForeignKey("posts.id"), primary_key=True),
    Column("hashtag_id", UUID, ForeignKey("hashtags.id"), primary_key=True),
)

mentions = Table(
    "mentions",
    metadata,
    Column("id", UUID, primary_key=True),
    Column("post_id", UUID, ForeignKey("posts.id")),
    Column("comment_id", UUID, ForeignKey("comments.id")),
    Column("username", Text, nullable=False),
    Column("created_at", TIMESTAMP),
)

comments = Table(
    "comments",
    metadata,
    Column("id", UUID, primary_key=True),
    Column("platform", Text, ForeignKey("platforms.name"), nullable=False),
    Column("platform_comment_id", Text, nullable=False),
    Column("post_id", UUID, ForeignKey("posts.id"), nullable=False),
    Column("author_id", UUID, ForeignKey("authors.id"), nullable=False),
    Column("parent_comment_id", UUID, ForeignKey("comments.id")),
    Column("content", Text, nullable=False),
    Column("likes", BigInteger),
    Column("reply_count", BigInteger),
    Column("posted_at", TIMESTAMP),
    Column("platform_metadata", JSONB),
    Column("created_at", TIMESTAMP),
    Column("updated_at", TIMESTAMP),
    Column("deleted_at", TIMESTAMP),
)

engagement = Table(
    "engagement",
    metadata,
    Column("id", UUID, primary_key=True),
    Column("post_id", UUID, ForeignKey("posts.id"), unique=True, nullable=False),
    Column("likes", BigInteger),
    Column("views", BigInteger),
    Column("shares", BigInteger),
    Column("comments_count", BigInteger),
    Column("saves", BigInteger),
    Column("reactions", JSONB),
    Column("created_at", TIMESTAMP),
    Column("updated_at", TIMESTAMP),
)

users = Table(
    "users",
    metadata,
    Column("id", UUID, primary_key=True),
    Column("email", Text, unique=True),
    Column("display_name", Text),
    Column("created_at", TIMESTAMP),
    Column("updated_at", TIMESTAMP),
)

conversations = Table(
    "conversations",
    metadata,
    Column("id", UUID, primary_key=True),
    Column("user_id", UUID, ForeignKey("users.id")),
    Column("title", Text),
    Column("is_archived", Boolean),
    Column("created_at", TIMESTAMP),
    Column("updated_at", TIMESTAMP),
    Column("deleted_at", TIMESTAMP),
)

messages = Table(
    "messages",
    metadata,
    Column("id", UUID, primary_key=True),
    Column("conversation_id", UUID, ForeignKey("conversations.id"), nullable=False),
    Column("role", Text, nullable=False),
    Column("content", Text, nullable=False),
    Column("sources", ARRAY(Text)),
    Column("sql_generated", Text),
    Column("model_used", Text),
    Column("execution_time_ms", Numeric),
    Column("prompt_tokens", Integer),
    Column("completion_tokens", Integer),
    Column("created_at", TIMESTAMP),
)

query_logs = Table(
    "query_logs",
    metadata,
    Column("id", UUID, primary_key=True),
    Column("conversation_id", UUID, ForeignKey("conversations.id")),
    Column("query_text", Text, nullable=False),
    Column("retrieved_document_ids", ARRAY(Text)),
    Column("filters_applied", JSONB),
    Column("latency_ms", Numeric),
    Column("created_at", TIMESTAMP),
)

assistant_logs = Table(
    "assistant_logs",
    metadata,
    Column("id", UUID, primary_key=True),
    Column("conversation_id", UUID, ForeignKey("conversations.id")),
    Column("message_id", UUID, ForeignKey("messages.id")),
    Column("prompt_used", Text, nullable=False),
    Column("sql_generated", Text),
    Column("model_used", Text, nullable=False),
    Column("execution_time_ms", Numeric),
    Column("token_usage", JSONB),
    Column("error", Text),
    Column("created_at", TIMESTAMP),
)

scrape_jobs = Table(
    "scrape_jobs",
    metadata,
    Column("id", UUID, primary_key=True),
    Column("platform", Text, ForeignKey("platforms.name"), nullable=False),
    Column("job_type", Text, nullable=False),
    Column("status", Text, nullable=False),
    Column("target", Text),
    Column("started_at", TIMESTAMP),
    Column("finished_at", TIMESTAMP),
    Column("records_scraped", Integer),
    Column("error", Text),
    Column("created_at", TIMESTAMP),
)

documents = Table(
    "documents",
    metadata,
    Column("id", UUID, primary_key=True),
    Column("source_type", Text, nullable=False),
    Column("source_id", UUID, nullable=False),
    Column("platform", Text, ForeignKey("platforms.name"), nullable=False),
    Column("content", Text, nullable=False),
    Column("metadata", JSONB),
    Column("created_at", TIMESTAMP),
)

embeddings = Table(
    "embeddings",
    metadata,
    Column("id", UUID, primary_key=True),
    Column("document_id", UUID, ForeignKey("documents.id"), nullable=False),
    Column("source_type", Text, nullable=False),
    Column("source_id", UUID, nullable=False),
    Column("platform", Text, ForeignKey("platforms.name"), nullable=False),
    Column("model", Text, nullable=False),
    Column("dimensions", Integer, nullable=False),
    Column("checksum", Text, nullable=False),
    Column("metadata", JSONB),
    Column("created_at", TIMESTAMP),
)

saved_searches = Table(
    "saved_searches",
    metadata,
    Column("id", UUID, primary_key=True),
    Column("name", Text, nullable=False),
    Column("kind", Text, nullable=False),
    Column("payload", JSONB),
    Column("created_at", TIMESTAMP),
    Column("updated_at", TIMESTAMP),
    Column("deleted_at", TIMESTAMP),
)

KNOWN_TABLES: frozenset[str] = frozenset(metadata.tables.keys())
