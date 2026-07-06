"""Human-readable schema description used to ground the AI assistant's SQL
generation prompts (see app/ai/sql_generator.py).

Kept as a static Python structure rather than introspected live from the
database so prompt construction never depends on a live DB connection and
stays fast/deterministic; update it alongside migrations/ when the schema
changes.
"""

from __future__ import annotations

SCHEMA_DESCRIPTION = """
platforms(id, name, display_name, is_active, created_at, updated_at)
authors(id, platform, platform_user_id, username, display_name, bio, is_verified,
        follower_count, following_count, post_count, location, created_at, deleted_at)
channels(id, platform, platform_channel_id, author_id -> authors.id, name,
         subscriber_count, video_count, total_views, created_at, deleted_at)
posts(id, platform, platform_post_id, author_id -> authors.id, content_type, caption,
      content, language, url, hashtags[], mentions[], posted_at, is_sponsored,
      created_at, deleted_at)
videos(id, platform, platform_video_id, channel_id -> channels.id, post_id -> posts.id,
       title, description, transcript, duration_seconds, published_at, deleted_at)
media(id, post_id -> posts.id, media_type, url, thumbnail_url, width, height, order_index)
hashtags(id, tag)
post_hashtags(post_id -> posts.id, hashtag_id -> hashtags.id)
mentions(id, post_id -> posts.id, comment_id -> comments.id, username)
comments(id, platform, platform_comment_id, post_id -> posts.id, author_id -> authors.id,
         parent_comment_id -> comments.id, content, likes, reply_count, posted_at, deleted_at)
engagement(id, post_id -> posts.id (unique), likes, views, shares, comments_count, saves,
           reactions jsonb)
conversations(id, user_id, title, is_archived, created_at, deleted_at)
messages(id, conversation_id -> conversations.id, role, content, sources[], sql_generated,
         model_used, execution_time_ms, created_at)
query_logs(id, conversation_id, query_text, retrieved_document_ids[], filters_applied jsonb,
           latency_ms, created_at)
assistant_logs(id, conversation_id, message_id, prompt_used, sql_generated, model_used,
               token_usage jsonb, error, created_at)
documents(id, source_type, source_id, platform, content, search_vector tsvector, created_at)
embeddings(id, document_id -> documents.id, source_type, source_id, platform, model,
           dimensions, vector, created_at)
scrape_jobs(id, platform, job_type, status, target, started_at, finished_at,
            records_scraped, error, created_at)

Notes:
- Only these tables have a `deleted_at` column (soft delete) -- filter
  `deleted_at is null` on THESE ONLY, using their own alias, unless the question
  explicitly asks about deleted/removed content: authors, channels, posts, videos,
  comments, conversations. No other table in this schema has a `deleted_at` column --
  in particular `engagement`, `media`, `hashtags`, `post_hashtags`, `mentions`,
  `messages`, `query_logs`, `assistant_logs`, `documents`, `embeddings`, `scrape_jobs`,
  and `platforms` do NOT have `deleted_at`; referencing it on those tables/aliases is a
  column-does-not-exist error.
- `engagement.likes/views/shares/comments_count/saves` are the authoritative engagement
  numbers; join `posts` -> `engagement` on `posts.id = engagement.post_id`.
- Every table's `platform` column (authors.platform, posts.platform, comments.platform,
  channels.platform, videos.platform, documents.platform, embeddings.platform,
  scrape_jobs.platform) is already the lowercase platform name itself
  (e.g. 'instagram', 'twitter', 'youtube') stored as plain text -- it is a foreign key to
  `platforms.name`, NOT to `platforms.id`. Filter it directly, e.g.
  `WHERE a.platform = 'instagram'` -- never join to `platforms` or compare it against
  `platforms.id` (a uuid); `platform = (SELECT id FROM platforms WHERE ...)` is a type
  error (text vs uuid) and will fail.
- Only SELECT/WITH statements are executable; never generate INSERT/UPDATE/DELETE/DDL.
""".strip()
