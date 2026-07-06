# `app/models/pydantic/` and `app/models/db/` — line-by-line and how it fits the app

This document covers every file in `app/models/pydantic/` (except `embedding.py`,
already covered in `docs/embedding_model_explained.md`) plus `app/models/db/`. Each
section explains every import, class, field, validator, and computed property — and
why it was written that way — then traces every real call site across `app/` and
`tests/` via `grep`, not assumption. Where something is defined but never actually
used by production code, that is called out explicitly, the same way
`EmbeddingDocument` was called out in the embedding doc.

**The big picture up front:** almost every class in `app/models/pydantic/` has a
1:1 counterpart repository in `app/repositories/` that sets a `model = <Class>`
class attribute (e.g. `AuthorRepository.model = Author`). `BaseRepository[ModelT]`
(`app/repositories/base.py`) is the generic that actually reads/writes Supabase —
it uses `model.model_computed_fields` to strip `@computed_field` properties before
insert/upsert (they aren't real columns) and `model.model_fields` to check whether
`deleted_at` exists before applying a soft-delete filter. So nearly every field
documented below has exactly one real consumer: the matching repository's
`_serialize`/`_deserialize`, called from `app/ingestion/pipeline.py` (write side)
and `app/retrieval` / `app/gradio` / `app/ai` (read side).

---

## 1. `app/models/pydantic/__init__.py`

```python
from app.models.pydantic.author import Author
from app.models.pydantic.channel import Channel, Video
from app.models.pydantic.comment import Comment, Reply, Thread
from app.models.pydantic.conversation import AssistantLog, ChatMessage, Conversation, QueryLog
from app.models.pydantic.embedding import EmbeddingDocument
from app.models.pydantic.engagement import Engagement
from app.models.pydantic.enums import (
    ContentType, EmbeddingSourceType, MediaType, MessageRole, PlatformName, ScrapeJobStatus,
)
from app.models.pydantic.hashtag import Hashtag, Mention, PostHashtag
from app.models.pydantic.media import Media
from app.models.pydantic.platform import Platform
from app.models.pydantic.post import Post

__all__ = [ ... ]
```

Pure re-export barrel: every domain model and enum in the package is imported here
and listed in `__all__`, so the rest of the app writes `from app.models.pydantic
import Author, Post, ...` instead of importing from each individual submodule.
This is why nearly every repository file (`app/repositories/*.py`) imports from
`app.models.pydantic` rather than `app.models.pydantic.author` etc. — grep
confirms every repository except `embedding_repository.py` and
`scrape_job_repository.py` uses the barrel import (those two instead import
`BaseSchema`/mixins/enums directly from `app.models.pydantic.base`/`.enums`
because they define their *own* local models, `Document`/`EmbeddingRow`/
`ScrapeJob`, that aren't part of this package — see `docs/embedding_model_explained.md`
section 3b and the `ScrapeJob` note under `enums.py` below).

Note `base.py`'s own exports (`BaseSchema`, `IdentifiedMixin`, `CreatedAtMixin`,
`TimestampMixin`, `SoftDeleteMixin`) are **not** re-exported here — callers that
need the mixins directly (every file in this package, plus
`embedding_repository.py` and `scrape_job_repository.py`) import
`app.models.pydantic.base` directly. Grep across `app/` confirms this: no file
does `from app.models.pydantic import BaseSchema`.

Every name in `__all__` is used somewhere in `app/` or `tests/` except two, both
documented in their own sections below: `Reply` and `Thread` (comment.py) are
exercised only by `tests/unit/test_models.py`, never by the live ingestion
pipeline or any repository.

---

## 2. `app/models/pydantic/base.py`

```python
"""Common base classes / mixins reused by every domain model."""
from __future__ import annotations
import uuid
from datetime import UTC, datetime
from pydantic import BaseModel, ConfigDict, Field
```
- `uuid` — for `IdentifiedMixin.id`.
- `UTC`, `datetime` — for the two `default_factory=lambda: datetime.now(UTC)`
  timestamp defaults, always timezone-aware (never naive `datetime.now()`) so
  comparisons against Postgres's `timestamptz` columns are unambiguous.
- `ConfigDict` — Pydantic v2's typed replacement for the old `class Config:` inner
  class.

### `class BaseSchema(BaseModel)`
```python
model_config = ConfigDict(
    populate_by_name=True,
    str_strip_whitespace=True,
    extra="ignore",
    use_enum_values=True,
)
```
The root of every domain model in the app (every class in this package inherits
it, directly or via another mixin). Four settings, each earning its place:
- `populate_by_name=True` — accept both an aliased key and the Python field name
  when constructing from a dict (relevant if a field ever grows an `alias=`).
- `str_strip_whitespace=True` — every `str` field is auto-`.strip()`ped, so
  scraped text (which frequently has leading/trailing whitespace from HTML/JSON)
  is clean without every normalizer function remembering to call `.strip()`
  itself.
- `extra="ignore"` — unknown keys in a dict passed to `model_validate`/`**kwargs`
  are silently dropped rather than raising. Necessary because rows read back from
  Supabase, or raw Apify actor output, routinely carry more keys than the model
  declares.
- `use_enum_values=True` — enum fields (e.g. `Author.platform: PlatformName`) are
  stored internally as their plain `str` value, not the enum member. This is why
  `Document.source_type` (in `embedding_repository.py`) reads back as a plain
  `str` even though the field's declared type is an enum — see
  `docs/embedding_model_explained.md`'s note on this exact behavior.

### `class IdentifiedMixin(BaseModel)`
```python
id: uuid.UUID = Field(default_factory=uuid.uuid4)
```
A client-generated UUID primary key. Every domain entity needs a stable `id` to
be referenced *before* it exists in Postgres — e.g. `Post.media: list[Media]` is
built with a placeholder `post_id=None` at normalization time, then
`app/ingestion/pipeline.py::_ingest_media` fixes up `post_id` to the
*persisted* post's id only after the post has actually been upserted (see
`model_copy(update={"post_id": persisted_post_id})` in `pipeline.py`). Used by
every model in this package except `PostHashtag` and `Thread` (both plain
`BaseSchema`, no independent identity of their own — `PostHashtag` is a join row,
`Thread` is a read-time aggregation, not a persisted entity).

### `class CreatedAtMixin(BaseModel)`
```python
created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
```
Just `created_at`, for append-only/log-style tables with no `updated_at` column:
`hashtags`, `mentions`, `media`, `documents`, `messages`, `query_logs`,
`assistant_logs` (per the migration files) — confirmed by grep, used by
`Hashtag`, `Mention`, `Media` (all `hashtag.py`/`media.py`), and `ChatMessage`,
`QueryLog`, `AssistantLog` (`conversation.py`), plus `Document` in
`embedding_repository.py`. The docstring explains *why* this must be a separate
mixin from `TimestampMixin` rather than just not using `updated_at`: PostgREST
rejects an insert payload containing a key with no matching column
("column not found in schema cache"), so serializing an `updated_at` field for a
table that doesn't have one would break every insert, not just look untidy.

### `class TimestampMixin(CreatedAtMixin)`
```python
updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
```
Extends `CreatedAtMixin` with `updated_at`, for tables that have both columns and
a trigger that maintains `updated_at` (see `migrations/0002_core_content_tables.sql`'s
`set_updated_at()` triggers on `authors`, `channels`, `posts`, `videos`,
`comments`, `engagement`, `platforms`, and `migrations/0003`'s trigger on
`conversations`). Used by `Author`, `Channel`, `Video`, `Post`, `Comment`,
`Conversation`, `Engagement`, `Platform` — every entity with a DB-maintained
`updated_at`.

### `class SoftDeleteMixin(BaseModel)`
```python
deleted_at: datetime | None = None

@property
def is_deleted(self) -> bool:
    return self.deleted_at is not None
```
Mirrors the nullable `deleted_at timestamptz` column present on every
"content" table (`migrations/0001`'s soft-delete convention: never hard-delete).
Used by `Author`, `Channel`, `Video`, `Post`, `Comment`, `Conversation` — the six
models with a `deleted_at` column. `BaseRepository.list_all` checks
`"deleted_at" in self.model.model_fields` to decide whether to apply
`.is_("deleted_at", "null")` at all, which is exactly how a model *without* this
mixin (e.g. `Engagement`, `Media`, `Hashtag`) safely shares the same `list_all`
method.

**Notable finding:** `is_deleted` is a plain `@property` (not `@computed_field`,
so it never gets serialized), and grepping all of `app/` and `tests/` for
`is_deleted` finds only its own definition here — it is never read anywhere else
in the codebase. `BaseRepository.soft_delete` sets `deleted_at` directly via
`update(record_id, {"deleted_at": ...})` rather than going through this property,
and no repository or UI code ever checks `record.is_deleted`. This is dead code
in the same sense `EmbeddingDocument` was found to be in the reference doc —
present for API completeness/readability, not on any live code path.

---

## 3. `app/models/pydantic/author.py`

```python
"""Unified author/channel-owner model. ...single normalized representation
so downstream code never branches on platform."""
from __future__ import annotations
from pydantic import Field, computed_field, field_validator
from app.models.pydantic.base import BaseSchema, IdentifiedMixin, SoftDeleteMixin, TimestampMixin
from app.models.pydantic.enums import PlatformName
```
- `computed_field` — exposes a derived `@property` as if it were a real model
  field (included in `model_dump()`/JSON schema by default). Used here for
  `dedup_key`.
- `field_validator` — single-field validation/normalization run at construction
  time. Used here for `username`.

### `class Author(IdentifiedMixin, TimestampMixin, SoftDeleteMixin, BaseSchema)`
Multiple inheritance gives `Author` an `id`, `created_at`/`updated_at`,
`deleted_at`/`is_deleted`, and `BaseSchema`'s config, on top of:

| Field | Type | Why |
|---|---|---|
| `platform` | `PlatformName` (required) | Which platform this profile belongs to — part of the natural dedup key. |
| `platform_user_id` | `str` (required) | The author's native ID on that platform (Instagram user id, X user id, YouTube channel id). The other half of the natural key. |
| `username` | `str` (required) | Normalized (see validator below) handle. |
| `display_name` | `str \| None` | Human-readable name, if the platform exposes one separately from username. |
| `bio` | `str \| None` | Profile bio/description text. |
| `profile_url` / `avatar_url` | `str \| None` | Direct links, carried through as-is. |
| `is_verified` / `is_private` | `bool = False` | Platform-reported flags. |
| `follower_count` / `following_count` / `post_count` | `int \| None`, `Field(ge=0)` | `None` (not `0`) means "the scraper didn't expose this," so "unknown" is never confused with a real zero. `ge=0` catches an obviously corrupt negative value at construction time. |
| `location` / `external_url` | `str \| None` | Optional profile metadata. |
| `platform_metadata` | `dict = Field(default_factory=dict)` | Free-form bag for anything platform-specific not worth a first-class column. |

```python
@field_validator("username")
@classmethod
def _normalize_username(cls, value: str) -> str:
    return value.lstrip("@").strip()
```
Strips a leading `@` (Twitter/X usernames are commonly scraped with the `@`
still attached) and any residual whitespace. Runs on every construction path,
including `model_validate` when reading a row back from Supabase.

```python
@computed_field  # type: ignore[prop-decorator]
@property
def dedup_key(self) -> str:
    return f"{self.platform}:{self.platform_user_id}"
```
`"{platform}:{platform_user_id}"` — the stable identity used to merge duplicate
`Author` records seen across multiple scrapes/pages within one ingestion run.
The `# type: ignore[prop-decorator]` comment works around a mypy limitation
stacking `@computed_field` under `@property` (also present on every other
`computed_field` in this package). Because it's a `computed_field`, it would
appear in `model_dump()` output — which is exactly why `BaseRepository._serialize`
explicitly pops every name in `model.model_computed_fields` before writing to
Supabase (there is no `dedup_key` column on `authors`).

**Where used** (grep across `app/` and `tests/`):
- Constructed by `normalize_author()` in `app/normalization/instagram.py:41`,
  `app/normalization/twitter.py:35`, `app/normalization/youtube.py:50` — one
  normalizer per platform, each mapping that platform's raw Apify JSON onto
  `Author`.
- `dedup_key` is read in `app/ingestion/pipeline.py` (`_build_id_map`, and
  `dedupe_by_key(result.authors, lambda a: a.dedup_key)` at line 143) and in
  `app/apify/instagram/scraper.py`, `app/apify/twitter/scraper.py`,
  `app/apify/youtube/scraper.py` via `get_or_register(authors_by_key,
  normalize_author(...), lambda a: a.dedup_key)` — see `app/normalization/common.py`
  (documented below) for why `get_or_register` (not just `dedupe_by_key`) is
  needed at the point an `Author` is first created.
- `AuthorRepository` (`app/repositories/author_repository.py`) sets
  `model = Author`, `table_name = "authors"`, and upserts on
  `on_conflict="platform,platform_user_id"` — exactly the two fields that make
  up `dedup_key`.
- Directly instantiated and asserted against in `tests/unit/test_models.py`
  (`test_author_dedup_key`) and used throughout `tests/conftest.py` fixtures /
  `tests/unit/test_repositories*.py`.

---

## 4. `app/models/pydantic/channel.py`

```python
"""Channel (YouTube-style) and Video models. Modeled separately from
Author/Post because "channel" carries subscriber semantics distinct from a
generic profile, and "video" carries duration/transcript semantics distinct
from a generic post — but both still funnel into the same Author/Post tables
via the normalization layer's mapping..."""
from __future__ import annotations
from datetime import datetime
from pydantic import Field, computed_field
from app.models.pydantic.base import BaseSchema, IdentifiedMixin, SoftDeleteMixin, TimestampMixin
from app.models.pydantic.enums import PlatformName
```

### `class Channel(IdentifiedMixin, TimestampMixin, SoftDeleteMixin, BaseSchema)`

| Field | Type | Notes |
|---|---|---|
| `platform` | `PlatformName` | Natural-key component 1. |
| `platform_channel_id` | `str` | Natural-key component 2. |
| `author_id` | `str` | FK to the owning `Author.id` — **note this is the client-side local id at construction time**; the ingestion pipeline remaps it to the persisted author id (see below). |
| `name` | `str` (required) | Channel display name. |
| `description` | `str \| None` | |
| `subscriber_count` / `video_count` / `total_views` | `int \| None`, `ge=0` | Same "`None` = unknown, never confused with 0" convention as `Author`. |
| `country` | `str \| None` | |
| `platform_metadata` | `dict` | |

```python
@computed_field
@property
def dedup_key(self) -> str:
    return f"{self.platform}:{self.platform_channel_id}"
```
Same pattern as `Author.dedup_key`.

**Where used:** built by `normalize_channel()` in `app/normalization/youtube.py:67`
(the only platform with a channel concept). `ChannelRepository`
(`app/repositories/channel_repository.py`) sets `model = Channel`,
`table_name = "channels"`, upserts on `on_conflict="platform,platform_channel_id"`.
`app/ingestion/pipeline.py::_run` remaps `author_id` via
`c.model_copy(update={"author_id": author_id_map.get(c.author_id, c.author_id)})`
before persisting — this is the FK-remap pattern used throughout the pipeline
(see section "Cross-file patterns" at the end of this document).

### `class Video(IdentifiedMixin, TimestampMixin, SoftDeleteMixin, BaseSchema)`

| Field | Type | Notes |
|---|---|---|
| `platform` | `PlatformName` | |
| `platform_video_id` | `str` | Natural key (with `platform`). |
| `channel_id` | `str` | FK to `Channel.id`, remapped post-persistence like `author_id` above. |
| `post_id` | `str \| None` | FK to the `Post` this video is *also* represented as (every video is normalized into a `Post` row too — see `normalize_post` in `youtube.py` — so retrieval/the assistant can treat it like any other post; `Video` carries the video-specific extras). |
| `title` | `str` (required) | |
| `description` | `str \| None` | |
| `transcript` | `str \| None` | Full video transcript text, if the transcript actor ran — this is the field `_generate_embeddings` embeds under `EmbeddingSourceType.TRANSCRIPT`. |
| `duration_seconds` | `float \| None`, `ge=0` | |
| `thumbnail_url` / `video_url` | `str \| None` | |
| `published_at` | `datetime \| None` | |
| `language` | `str \| None` | |
| `platform_metadata` | `dict` | |

```python
@computed_field
@property
def dedup_key(self) -> str:
    return f"{self.platform}:{self.platform_video_id}"

@computed_field
@property
def has_transcript(self) -> bool:
    return bool(self.transcript and self.transcript.strip())
```
`has_transcript` guards against a transcript field that is present but blank
(whitespace-only) — `bool("")` is already falsy but `bool("   ")` is not, hence
the explicit `.strip()`.

**Where used:** built by `normalize_video()` in `app/normalization/youtube.py:122`.
`VideoRepository` (`app/repositories/channel_repository.py`) sets `model = Video`,
upserts on `on_conflict="platform,platform_video_id"`. `dedup_key` used in
`pipeline.py`'s `dedupe_by_key(result.videos, lambda v: v.dedup_key)` (line 172)
and its FK remap (`channel_id`/`post_id`, lines 173-181).
**`has_transcript` itself, however, is never read anywhere outside its own
definition and `tests/unit/test_models.py`** (`test_video_has_transcript_*`,
3 tests) — `app/ingestion/pipeline.py::_generate_embeddings` decides whether to
embed a video's transcript by checking `video.transcript` truthiness directly
(not via `has_transcript`), so this computed field is unused in the live path,
same category of finding as `SoftDeleteMixin.is_deleted`.

---

## 5. `app/models/pydantic/comment.py`

```python
"""Comment / Reply / Thread models. A `Reply` is modeled as a `Comment` with
`parent_comment_id` set, rather than a separate class hierarchy... `Thread`
groups a root comment with its replies for the retrieval/assistant layer..."""
from __future__ import annotations
from datetime import datetime
from pydantic import Field, computed_field, field_validator
from app.models.pydantic.base import BaseSchema, IdentifiedMixin, SoftDeleteMixin, TimestampMixin
from app.models.pydantic.enums import PlatformName
```

### `class Comment(IdentifiedMixin, TimestampMixin, SoftDeleteMixin, BaseSchema)`

| Field | Type | Notes |
|---|---|---|
| `platform` | `PlatformName` | |
| `platform_comment_id` | `str` | Natural key (with `platform`). |
| `post_id` | `str` (required) | FK to the parent `Post`, remapped post-persistence. |
| `author_id` | `str` (required) | FK to the commenter's `Author`, remapped post-persistence. |
| `parent_comment_id` | `str \| None` | Set for nested replies; `None` for a top-level comment. |
| `content` | `str` (required) | The comment text — validated non-empty (see below). |
| `language` | `str \| None` | |
| `likes` | `int \| None`, `ge=0` | |
| `reply_count` | `int \| None`, `ge=0` | |
| `hashtags` / `mentions` | `list[str]` | Extracted from `content` by the normalizer (`extract_hashtags`/`extract_mentions` in `app/normalization/common.py`'s sibling text-utils, actually in `app/utils/text.py` — imported by each platform normalizer). |
| `posted_at` | `datetime \| None` | |
| `platform_metadata` | `dict` | |

```python
@field_validator("content")
@classmethod
def _non_empty(cls, value: str) -> str:
    if not value or not value.strip():
        raise ValueError("Comment content cannot be empty")
    return value
```
Rejects a blank comment at construction time — every platform normalizer already
guards against this upstream with `text or "(no text)"` fallback (see
`normalize_comment` in `instagram.py`/`twitter.py`/`youtube.py`), so in practice
this validator is a defense-in-depth invariant, not something callers are
expected to trigger.

```python
@computed_field
@property
def dedup_key(self) -> str:
    return f"{self.platform}:{self.platform_comment_id}"

@computed_field
@property
def is_reply(self) -> bool:
    return self.parent_comment_id is not None
```

**Where used:** constructed by `normalize_comment()` in
`app/normalization/instagram.py:128`, `twitter.py:121`, `youtube.py:151`.
`CommentRepository` (`app/repositories/comment_repository.py`) sets
`model = Comment`, upserts on `on_conflict="platform,platform_comment_id"`, and
exposes `replies_to(parent_comment_id)`. `dedup_key` drives
`pipeline.py`'s dedupe/remap (line 187) and the two-pass "relink" trick
described in the cross-file section below. **`is_reply` itself is never read
outside its own definition and `tests/unit/test_models.py`**
(`test_comment_is_reply_false_without_parent`,
`test_comment_is_reply_true_with_parent`) — the pipeline instead checks
`comment.parent_comment_id` truthiness directly (see
`_relink_comment_parents` in `pipeline.py:220`: `if not comment.parent_comment_id:
continue`), so like `has_transcript`/`is_deleted`, this is a defined-but-unused
convenience property in the live path.

### `class Reply(Comment)`
```python
class Reply(Comment):
    """Semantic alias for a Comment whose parent_comment_id is required."""
    parent_comment_id: str
```
Overrides the inherited `parent_comment_id: str | None = None` with a required
`str`, expressing "a Reply is a Comment that is guaranteed to have a parent" at
the type level, without duplicating every other `Comment` field.

**Where used:** grep across all of `app/` finds **zero** production call sites.
The only place `Reply` is instantiated is `tests/unit/test_models.py` (lines 144
and 154, `test_thread_total_participants` and a reply-construction test) —
`app/normalization/*.py`'s `normalize_comment()` and
`app/ingestion/pipeline.py` both construct nested comments as a plain `Comment`
with `parent_comment_id` set, never as `Reply`. This mirrors the
`EmbeddingDocument` finding from the reference doc: `Reply` is a clean,
spec-shaped convenience type that exists and is unit-tested, but the real
ingestion pipeline doesn't use it.

### `class Thread(BaseSchema)`
```python
class Thread(BaseSchema):
    root: Comment
    replies: list[Comment] = Field(default_factory=list)

    @computed_field
    @property
    def total_participants(self) -> int:
        return len({c.author_id for c in [self.root, *self.replies]})
```
A read-time aggregation (not `IdentifiedMixin`/`TimestampMixin` — it isn't a
persisted row, it's assembled on demand from already-persisted comments) meant
to group a root comment with its replies "for the retrieval/assistant layer to
consume as a single conversational unit" per the module docstring.
`total_participants` counts the distinct `author_id`s across the root and every
reply via a set comprehension.

**Where used:** grep finds `Thread` only in `app/models/pydantic/comment.py`
(definition), `app/models/pydantic/__init__.py` (re-export), and
`tests/unit/test_models.py` (`test_thread_total_participants`, lines 165-170).
**No repository, retrieval service, or Gradio UI code ever constructs a
`Thread`.** `RetrievalService`/`CommentRepository.replies_to()` return flat
`list[Comment]`, and nothing in `app/retrieval/` or `app/gradio/` groups them
into a `Thread` object. So — like `Reply` and `EmbeddingDocument` — `Thread` is
a defined, tested, spec-matching shape that the live app never actually
constructs.

---

## 6. `app/models/pydantic/conversation.py`

```python
"""Chat/assistant persistence models: Conversation, ChatMessage, QueryLog.
These back the "store every user query and AI response" requirement..."""
from __future__ import annotations
from pydantic import Field, computed_field
from app.models.pydantic.base import (
    BaseSchema, CreatedAtMixin, IdentifiedMixin, SoftDeleteMixin, TimestampMixin,
)
from app.models.pydantic.enums import MessageRole
```

### `class Conversation(IdentifiedMixin, TimestampMixin, SoftDeleteMixin, BaseSchema)`

| Field | Type | Notes |
|---|---|---|
| `user_id` | `str \| None` | Optional — the app supports anonymous conversations (no login is wired into the Gradio UI). |
| `title` | `str \| None` | User- or auto-set conversation title. |
| `is_archived` | `bool = False` | |

```python
@computed_field
@property
def display_title(self) -> str:
    return self.title or "New conversation"
```
Fallback label for the UI when no title has been set yet.

**Where used:** `AssistantChatService`/`ai/assistant.py:119` constructs
`Conversation(title=question[:60])` (auto-titles a new conversation from the
first 60 chars of the user's question) when no `conversation_id` was supplied;
`app/services/chat_service.py:29` does the same for a different entry point.
`ConversationRepository` (`app/repositories/conversation_repository.py`) sets
`model = Conversation`. `display_title` is read in
`app/services/chat_service.py:49` (`f"# {conversation.display_title}"`, when
rendering a conversation transcript) and `app/gradio/chat_tab.py:48`
(`[(c.display_title, str(c.id)) for c in conversations]`, populating the
Gradio conversation-picker dropdown) — an actively used computed field, unlike
several others in this package.

### `class ChatMessage(IdentifiedMixin, CreatedAtMixin, BaseSchema)`
No `TimestampMixin`/`SoftDeleteMixin` — messages are immutable once written
(`migrations/0003`'s `messages` table has no `updated_at`/`deleted_at` column).

| Field | Type | Notes |
|---|---|---|
| `conversation_id` | `str` (required) | FK to `Conversation.id`. |
| `role` | `MessageRole` (required) | `user` / `assistant` / `system`. |
| `content` | `str` (required) | Message text. |
| `sources` | `list[str]`, `Field(description="Cited record IDs / URLs")` | Which retrieved records/SQL results backed this answer — the audit trail. |
| `sql_generated` | `str \| None` | The SQL the AI generated for this turn, if any. |
| `model_used` | `str \| None` | Which LLM produced this message. |
| `execution_time_ms` | `float \| None`, `ge=0` | |
| `prompt_tokens` / `completion_tokens` | `int \| None`, `ge=0` | Token accounting for cost tracking. |

**Where used:** constructed in `app/ai/assistant.py` at line 126 (the user's
turn) and lines 192-204 (the assistant's turn, with `sources`, `sql_generated`,
`model_used`, timing, and token counts all populated from the live response).
`MessageRepository` (`app/repositories/message_repository.py`) sets
`model = ChatMessage`, exposes `by_conversation(...)` used to reconstruct chat
history in `assistant.py:122`.

### `class QueryLog(IdentifiedMixin, CreatedAtMixin, BaseSchema)`
```python
"""Raw log of every user query, independent of the chat message it produced —
kept separate so query analytics survive even if the chat UI/message schema
changes."""
```
| Field | Type | Notes |
|---|---|---|
| `conversation_id` | `str \| None` | |
| `query_text` | `str` (required) | The raw user question. |
| `retrieved_document_ids` | `list[str]` | Which documents/rows were pulled in to answer it. |
| `filters_applied` | `dict` | Any platform/date filters the user applied. |
| `latency_ms` | `float \| None`, `ge=0` | |

**Where used:** constructed in `app/ai/assistant.py:207-213` at the end of every
`ask()` call, regardless of whether SQL generation or retrieval succeeded (per
the module's docstring: "persisting the full turn ... regardless of whether SQL
generation or retrieval succeeded"). `QueryLogRepository`
(`app/repositories/query_log_repository.py`) sets `model = QueryLog`.

### `class AssistantLog(IdentifiedMixin, CreatedAtMixin, BaseSchema)`
```python
"""Log of the assistant's generation step: prompt, model, SQL, timing."""
```
| Field | Type | Notes |
|---|---|---|
| `conversation_id` / `message_id` | `str \| None` | Link back to the conversation and the specific `ChatMessage` this generation produced. |
| `prompt_used` | `str` (required) | The full prompt text sent to the LLM (truncated to 4000 chars at the call site). |
| `sql_generated` | `str \| None` | |
| `model_used` | `str` (required) | |
| `execution_time_ms` | `float \| None`, `ge=0` | |
| `token_usage` | `dict[str, int]` | `{"prompt_tokens": ..., "completion_tokens": ...}`. |
| `error` | `str \| None` | Populated if generation failed (field exists but `assistant.py`'s current code path always constructs a success case — `error` is set by nothing in the current codebase; it's a forward-compatible column). |

**Where used:** constructed in `app/ai/assistant.py:215-227`, the last of the
four records persisted per turn. `AssistantLogRepository`
(`app/repositories/query_log_repository.py`, same file as `QueryLogRepository`)
sets `model = AssistantLog`.

---

## 7. `app/models/pydantic/engagement.py`

```python
"""Engagement metrics, normalized across platforms. Not every platform exposes
every metric (e.g. Instagram rarely exposes shares); missing values are `None`
rather than `0` so "unknown" is never confused with "zero"."""
from __future__ import annotations
from pydantic import Field, computed_field
from app.models.pydantic.base import BaseSchema, IdentifiedMixin, TimestampMixin
```
No `SoftDeleteMixin` — `engagement` rows aren't independently soft-deletable
(they die with their post via `on delete cascade` per `migrations/0002`).

### `class Engagement(IdentifiedMixin, TimestampMixin, BaseSchema)`

| Field | Type | Notes |
|---|---|---|
| `post_id` | `str \| None` | FK to `Post.id`; `unique` on the `engagement` table (one engagement row per post) — remapped post-persistence like every other FK. |
| `likes` / `views` / `shares` / `comments_count` / `saves` | `int \| None`, `ge=0` | Per-platform metrics; `None` when the platform doesn't expose that metric. |
| `reactions` | `dict[str, int]` | Platform-specific reaction breakdown (e.g. Facebook's like/love/haha/wow/sad/angry), keyed by reaction name. |

```python
@computed_field
@property
def total_engagement(self) -> int:
    """Sum of every known engagement signal; used for ranking/sorting."""
    return sum(
        v for v in (self.likes, self.shares, self.comments_count, self.saves) if v is not None
    ) + sum(self.reactions.values())

@computed_field
@property
def engagement_rate(self) -> float | None:
    """total_engagement / views, when views are known — a common
    cross-platform comparability metric requested by the AI assistant."""
    if not self.views:
        return None
    return round(self.total_engagement / self.views, 6)
```
`total_engagement` deliberately excludes `views` from the sum itself (views is
the denominator for `engagement_rate`, and mixing it into the numerator would
double count / make the rate meaningless). Guards against `None` values with a
generator-expression filter rather than requiring all fields to be populated.
`engagement_rate` guards against both `None` and `0` views (`if not self.views`)
to avoid a `ZeroDivisionError`, returning `None` for "can't be computed" rather
than a fake `0.0`.

**Where used:** built by `extract_engagement()` (per-platform, called
`Engagement(...)` at `app/normalization/instagram.py:149`, `twitter.py:143`,
`youtube.py:183`). `EngagementRepository`
(`app/repositories/engagement_repository.py`) sets `model = Engagement`,
`upsert_for_post` on `on_conflict="post_id"`. `app/ingestion/pipeline.py`'s
`_ingest_engagement` (line 301) calls `normalizer.extract_engagement(post)`
then remaps `post_id` via `model_copy`. **`total_engagement`** is read in
`app/gradio/analytics_tab.py:63` (`"total_engagement": p.total_engagement`,
feeding the analytics dashboard's data table). **`engagement_rate`, however, is
never read anywhere outside its own definition and
`tests/unit/test_models.py`** (`test_engagement_rate_none_when_no_views`,
`test_engagement_rate_none_when_zero_views`,
`test_engagement_rate_computed_when_views_present`) — despite the docstring
calling it "a common cross-platform comparability metric requested by the AI
assistant," no code in `app/ai/`, `app/retrieval/`, or `app/gradio/` currently
reads `engagement_rate`. It's a third instance of the "defined per spec,
unit-tested, not wired into the live app" pattern seen with
`EmbeddingDocument`/`Reply`/`Thread`.

---

## 8. `app/models/pydantic/enums.py`

```python
"""Shared enums used across every Pydantic model and platform scraper."""
from __future__ import annotations
from enum import StrEnum
```
`StrEnum` (Python 3.11+) is a `str` subclass — members compare equal to their
plain string value and serialize to JSON as a bare string automatically, which
is exactly what `BaseSchema`'s `use_enum_values=True` relies on (a stored enum
member behaves identically to the string PostgREST expects).

### `class PlatformName(StrEnum)`
```python
INSTAGRAM = "instagram"
TWITTER = "twitter"
YOUTUBE = "youtube"
# Reserved for future extensibility (see docs/architecture.md).
REDDIT = "reddit"
LINKEDIN = "linkedin"
FACEBOOK = "facebook"
TIKTOK = "tiktok"
NEWS = "news"
```
Docstring: "Adding a platform here plus a matching scraper + normalizer is the
only change needed to onboard a new source; no existing model changes shape."
**Only `INSTAGRAM`, `TWITTER`, `YOUTUBE` are actually wired to a scraper +
normalizer today** (`app/apify/instagram`, `app/apify/twitter`,
`app/apify/youtube`, and `app/normalization/{instagram,twitter,youtube}.py`,
registered in the `NORMALIZERS` dict in `app/normalization/__init__.py`). The
other five members (`REDDIT`, `LINKEDIN`, `FACEBOOK`, `TIKTOK`, `NEWS`) exist
only as enum values — used in `migrations/0002`'s seed `insert into platforms`
statement and referenceable by any `Platform` row, but no scraper/normalizer
targets them yet, consistent with the inline "Reserved for future
extensibility" comment.

**Where used:** every domain model with a `platform: PlatformName` field
(`Author`, `Channel`, `Video`, `Post`, `Comment`, `Platform`), every normalizer
(`app/normalization/*.py`, tagging output with the right platform), every
scraper (`app/apify/*/scraper.py`), and `ScrapeJob.platform`
(`app/repositories/scrape_job_repository.py`).

### `class MediaType(StrEnum)`
```python
IMAGE = "image"; VIDEO = "video"; AUDIO = "audio"; CAROUSEL = "carousel"; GIF = "gif"; OTHER = "other"
```
**Where used:** `Media.media_type` field (`media.py`). Only `IMAGE` and `VIDEO`
are actually constructed by any normalizer (grep confirms
`MediaType.IMAGE`/`MediaType.VIDEO` in `instagram.py`, `twitter.py`,
`youtube.py`) — `AUDIO`, `CAROUSEL`, `GIF`, `OTHER` are declared for schema
completeness (a carousel post's *children* are normalized as individual
image/video `Media` items, not as one `CAROUSEL`-typed item — see
`instagram.py`'s carousel handling) but never actually assigned today.

### `class ContentType(StrEnum)`
```python
"""The kind of top-level content a Post represents."""
POST = "post"; REEL = "reel"; STORY = "story"; TWEET = "tweet"; RETWEET = "retweet"
QUOTE = "quote"; VIDEO = "video"; SHORT = "short"; LIVE = "live"
```
**Where used:** `Post.content_type` field. Actively assigned: `POST`/`REEL`/
`VIDEO` (Instagram, via `_CONTENT_TYPE_MAP` in `instagram.py`), `TWEET`/
`RETWEET`/`QUOTE` (Twitter, in `twitter.py`), `VIDEO`/`SHORT` (YouTube, by
duration threshold in `youtube.py`). `STORY` and `LIVE` are declared but never
assigned by any current normalizer (Instagram Stories/Lives aren't scraped by
the current actors).

### `class MessageRole(StrEnum)`
```python
USER = "user"; ASSISTANT = "assistant"; SYSTEM = "system"
```
**Where used:** `ChatMessage.role`. `USER` and `ASSISTANT` are assigned in
`app/ai/assistant.py` (lines 126, 195). `SYSTEM` is declared (and present in the
`messages` table's check constraint in `migrations/0003`) but no code in
`app/ai/assistant.py` currently persists a `SYSTEM`-role `ChatMessage` — system
prompts are sent to the LLM inline (`ASSISTANT_SYSTEM_PROMPT`,
`CONVERSATION_MEMORY_PROMPT`) but never written to the `messages` table as a row
of their own.

### `class EmbeddingSourceType(StrEnum)`
```python
POST = "post"; COMMENT = "comment"; CAPTION = "caption"; DESCRIPTION = "description"; TRANSCRIPT = "transcript"
```
Fully documented in `docs/embedding_model_explained.md` (used by
`EmbeddingDocument`, `Document`, `EmbeddingRow`, `EmbeddableItem`).
**Cross-check against the actual embedding call sites**
(`app/ingestion/pipeline.py::_generate_embeddings`, lines 333/343/353): only
`POST`, `COMMENT`, and `TRANSCRIPT` are ever passed — a post's caption/body text
is embedded tagged as `EmbeddingSourceType.POST` (not `CAPTION`), and a video's
description is never separately embedded at all. So `CAPTION` and
`DESCRIPTION` are declared enum members with **zero** production call sites —
grep across all of `app/` and `tests/` finds them only in `enums.py` itself.

### `class ScrapeJobStatus(StrEnum)`
```python
PENDING = "pending"; RUNNING = "running"; SUCCEEDED = "succeeded"; FAILED = "failed"; PARTIAL = "partial"
```
**Where used:** all five members are actively used by `ScrapeJob.status` and
`ScrapeJobRepository` (`app/repositories/scrape_job_repository.py`) — note
`ScrapeJob` is a *repository-local* Pydantic model (not part of this
`app/models/pydantic` package, same pattern as `Document`/`EmbeddingRow` in
`embedding_repository.py`): `class ScrapeJob(IdentifiedMixin, BaseSchema)`,
using `IdentifiedMixin` and `BaseSchema` from `app.models.pydantic.base`
directly, plus `PlatformName`/`ScrapeJobStatus` from `.enums` directly, but
*not* re-exported through `app/models/pydantic/__init__.py`. `start()` sets
`PENDING`→`RUNNING`; `mark_succeeded`/`mark_partial`/`mark_failed` (called from
`app/ingestion/pipeline.py::ingest`, lines 131-138) set `SUCCEEDED`/`PARTIAL`/
`FAILED` respectively.

---

## 9. `app/models/pydantic/hashtag.py`

```python
"""Hashtag and Mention models, plus the join-table representation for
`post_hashtags` (kept as a lightweight model rather than a bare tuple so the
repository layer has a typed object to insert)."""
from __future__ import annotations
from pydantic import field_validator
from app.models.pydantic.base import BaseSchema, CreatedAtMixin, IdentifiedMixin
```
No `TimestampMixin`/`SoftDeleteMixin` — hashtags/mentions are append-only,
never updated or soft-deleted (matches `migrations/0002`: no `updated_at`/
`deleted_at` columns on `hashtags`, `mentions`, or `post_hashtags`).

### `class Hashtag(IdentifiedMixin, CreatedAtMixin, BaseSchema)`
```python
tag: str

@field_validator("tag")
@classmethod
def _normalize_tag(cls, value: str) -> str:
    return value.lstrip("#").strip().lower()
```
Strips a leading `#`, whitespace, and lower-cases — so `"#Python"`, `"python "`,
and `"PYTHON"` all normalize to the same `tag` value and collide on the
`hashtags.tag unique` constraint instead of creating duplicate rows.

**Where used:** constructed in `app/ingestion/pipeline.py:257`
(`Hashtag(tag=tag) for tag in all_tags`, inside `_ingest_hashtags`, over the
union of every post's already-lower-cased `hashtags` list — see `Post`'s own
`_lower_list` validator below, so by the time this constructor runs the tag is
already normalized; the validator here is a second line of defense plus what
protects any *other* caller). `HashtagRepository`
(`app/repositories/hashtag_repository.py`) sets `model = Hashtag`,
`upsert_tag`/`bulk_upsert_tags` on `on_conflict="tag"`.

### `class Mention(IdentifiedMixin, CreatedAtMixin, BaseSchema)`
```python
post_id: str | None = None
comment_id: str | None = None
username: str

@field_validator("username")
@classmethod
def _normalize_username(cls, value: str) -> str:
    return value.lstrip("@").strip().lower()
```
Both `post_id` and `comment_id` are optional because a mention can belong to
either a post or a comment (the `mentions` table's check constraint,
`migrations/0002` line 174, requires *at least one* to be non-null — not
enforced at the Pydantic layer, only at the DB layer).

**Where used:** constructed in `app/ingestion/pipeline.py:290`
(`Mention(post_id=persisted_post_id, username=username)` inside
`_ingest_mentions`). `MentionRepository`
(`app/repositories/mention_repository.py`) sets `model = Mention`, exposes
`by_post`, `by_comment`, `by_username`, `bulk_create_mentions` (plain `insert`,
not `upsert` — mentions aren't deduplicated by a DB constraint, only by the
pipeline's own `existing_usernames` check before construction, see
`pipeline.py:288`).

### `class PostHashtag(BaseSchema)`
```python
"""Join-table row linking a Post to a Hashtag."""
post_id: str
hashtag_id: str
```
No `IdentifiedMixin` — the `post_hashtags` table's primary key is the
composite `(post_id, hashtag_id)`, not a surrogate `id` column
(`migrations/0002` line 140).

**Where used:** constructed in `app/ingestion/pipeline.py:272`
(`PostHashtag(post_id=persisted_post_id, hashtag_id=hashtag_id)` inside
`_ingest_hashtags`). `PostHashtagRepository`
(`app/repositories/hashtag_repository.py`) sets `model = PostHashtag`,
`bulk_link`/`link` upsert on `on_conflict="post_id,hashtag_id"`.

---

## 10. `app/models/pydantic/media.py`

```python
"""Media attachment model (images/videos/audio attached to a post/comment)."""
from __future__ import annotations
from pydantic import Field, field_validator
from app.models.pydantic.base import BaseSchema, CreatedAtMixin, IdentifiedMixin
from app.models.pydantic.enums import MediaType
```

### `class Media(IdentifiedMixin, CreatedAtMixin, BaseSchema)`

| Field | Type | Notes |
|---|---|---|
| `post_id` | `str \| None` | `None` at normalization time (media is attached to a `Post` object in-memory before the post is persisted — see `Post.media` below); fixed up post-persistence. |
| `media_type` | `MediaType` (required) | |
| `url` | `str` (required) | Validated http(s) (see below). |
| `thumbnail_url` | `str \| None` | |
| `width` / `height` | `int \| None`, `ge=0` | |
| `duration_seconds` | `float \| None`, `ge=0` | For video/audio. |
| `file_size_bytes` | `int \| None`, `ge=0` | |
| `alt_text` | `str \| None` | Accessibility text, if the platform exposes it. |
| `order_index` | `int = 0` | Position within a multi-media post (e.g. carousel slide order). |

```python
@field_validator("url")
@classmethod
def _must_be_http(cls, value: str) -> str:
    if not value.startswith(("http://", "https://")):
        raise ValueError(f"Media url must be http(s): {value!r}")
    return value
```
Cheap sanity check that the URL is actually a fetchable web URL, catching a
malformed/relative URL from a scraper bug before it's persisted.

**Where used:** constructed by every normalizer's media-extraction logic:
`app/normalization/instagram.py:84,89,97` (image/carousel-child/video),
`app/normalization/twitter.py:87`, `app/normalization/youtube.py:94`.
`MediaRepository` (`app/repositories/media_repository.py`) sets
`model = Media`, `by_post`, `bulk_create_media` (plain insert — media rows
aren't deduplicated by a DB unique constraint, only by the pipeline's own
`existing_urls` check, see `pipeline.py:239`). Consumed as `Post.media` (see
below) and remapped/created in `app/ingestion/pipeline.py::_ingest_media`
(lines 230-248).

**Schema-drift note:** `alt_text` and `file_size_bytes` are declared on the
Pydantic model and on the real `media` table (`migrations/0002` lines 124-125),
but grep finds **no normalizer ever sets them** — every `Media(...)` call site
in `app/normalization/*.py` omits both, so they are always `None` in practice
today, even though the column and field both exist end-to-end.

Separately, `app/models/db/orm.py`'s `media` Table object (a *different*,
SQLAlchemy Core mirror of this same table, see section 14) is missing four
columns the real migration and this Pydantic model both have:
`thumbnail_url`, `duration_seconds`, `file_size_bytes`, `alt_text`. See the
"orm.py column drift" note in section 14 for why this doesn't break anything
functionally but does mean `orm.py` can't be trusted as a complete schema
reference for this table.

---

## 11. `app/models/pydantic/platform.py`

```python
"""Platform reference model — a row per supported source (instagram, x, ...)."""
from __future__ import annotations
from app.models.pydantic.base import BaseSchema, IdentifiedMixin, TimestampMixin
from app.models.pydantic.enums import PlatformName
```

### `class Platform(IdentifiedMixin, TimestampMixin, BaseSchema)`
```python
name: PlatformName
display_name: str
is_active: bool = True
```
No `SoftDeleteMixin` (platforms row is a small, static reference table — see
`migrations/0002`'s seed `insert into platforms` values for
instagram/twitter/youtube/reddit/linkedin/facebook/tiktok/news — not something
that gets soft-deleted). `name` is the FK target every content table's
`platform` column references (`ForeignKey("platforms.name")` — a text FK, not
a UUID FK, per `app/models/db/orm.py`).

**Where used:** grep finds **no direct `Platform(...)` construction anywhere in
`app/`** — the only place `Platform` is instantiated at all is
`tests/unit/test_repositories_extended.py:802`
(`Platform(name=PlatformName.INSTAGRAM, display_name="Instagram")`). In
production code, `Platform` is used purely as the repository's generic type
parameter and `model` attribute: `PlatformRepository(BaseRepository[Platform])`
with `model = Platform` (`app/repositories/platform_repository.py`), which
exposes `get_by_name(name)` used nowhere else that grep can find in `app/`
either (the eight rows are seeded directly by the migration's `insert into
platforms`, not created via this repository at runtime — the app currently
only *reads* platform rows implicitly through FK references, never queries the
`platforms` table itself through `PlatformRepository.get_by_name`). This is
close to, but not quite as extreme as, the `EmbeddingDocument`/`Reply`/`Thread`
finding: the class and its repository exist, are wired together correctly, and
are exercised by tests, but nothing in the live request/ingestion path
currently calls `PlatformRepository.get_by_name` or constructs a `Platform`.

---

## 12. `app/models/pydantic/post.py`

```python
"""Unified Post model — the central content entity every platform maps into."""
from __future__ import annotations
from datetime import datetime
from pydantic import Field, computed_field, field_validator
from app.models.pydantic.base import BaseSchema, IdentifiedMixin, SoftDeleteMixin, TimestampMixin
from app.models.pydantic.enums import ContentType, PlatformName
from app.models.pydantic.media import Media
```
The only cross-import between sibling domain-model files in this package:
`post.py` imports `Media` from `media.py` because `Post.media` embeds a list of
`Media` objects directly (see below) rather than referencing them by id.

### `class Post(IdentifiedMixin, TimestampMixin, SoftDeleteMixin, BaseSchema)`

| Field | Type | Notes |
|---|---|---|
| `platform` | `PlatformName` | |
| `platform_post_id` | `str`, `Field(..., description="Native post ID on the platform")` | Natural key (with `platform`). |
| `author_id` | `str` (required) | FK, remapped post-persistence. |
| `content_type` | `ContentType` (required) | post/reel/tweet/etc. |
| `caption` / `content` | `str \| None` | Two separate text fields — `caption` is the platform's short display text (e.g. Instagram caption, video title), `content` is the fuller body text where the two differ (see YouTube's `normalize_post`: `caption=str(raw.get("title", ""))`, `content=description` — distinct values). |
| `language` | `str \| None` | |
| `url` | `str \| None` | Canonical link to the content. |
| `hashtags` / `mentions` | `list[str]` | Normalized (see validator below). |
| `urls` | `list[str]` | Any URLs extracted from the caption/content text. |
| `media` | `list[Media] = Field(default_factory=list, exclude=True)` | See below — **not** a real `posts` column. |
| `posted_at` | `datetime \| None` | |
| `is_pinned` / `is_sponsored` | `bool = False` | |
| `location` | `str \| None` | |
| `platform_metadata` | `dict` | |

```python
# Not a `posts` column -- media lives in its own table, linked by
# `post_id`. Carried here only so scrapers/normalizers can hand a
# post's media along in one object; `exclude=True` keeps it out of the
# payload `BaseRepository` sends to the `posts` table (see
# `app/ingestion/pipeline.py::_ingest_media`, which reads this field
# directly off the in-memory `Post`, not via a DB round-trip).
media: list[Media] = Field(default_factory=list, exclude=True)
```
This is the one field in the whole package with `exclude=True` set explicitly
at the `Field()` level (as opposed to being dropped dynamically via
`model_computed_fields`, the mechanism `BaseRepository._serialize` uses for
computed properties). `exclude=True` means `model_dump()` never includes
`media` at all, at any call site — a stronger, unconditional exclusion, needed
because `media` isn't a computed field (it's a normal declared field with
real, mutable data in it), so the computed-field-stripping logic in
`BaseRepository._serialize` wouldn't have caught it.

```python
@field_validator("hashtags", "mentions", mode="before")
@classmethod
def _lower_list(cls, value: list[str] | None) -> list[str]:
    if not value:
        return []
    return [v.lstrip("#@").lower() for v in value]
```
`mode="before"` runs before Pydantic's own list/str type coercion, so it can
handle `None` (coerced to `[]`) as well as normalize each string (strip a
leading `#`/`@`, whichever the field happens to contain, and lower-case). One
validator function attached to two fields (`"hashtags", "mentions"`) since the
normalization logic is identical for both.

```python
@computed_field
@property
def dedup_key(self) -> str:
    """Stable key used to detect duplicate posts across ingestion runs."""
    return f"{self.platform}:{self.platform_post_id}"

@computed_field
@property
def has_media(self) -> bool:
    return len(self.media) > 0
```

**Where used:**
- Constructed by `normalize_post()` in `app/normalization/instagram.py:99`,
  `twitter.py:89`, `youtube.py:97`.
- `dedup_key` drives `pipeline.py`'s `dedupe_by_key(result.posts, lambda p:
  p.dedup_key)` (line 161) and `_build_id_map`.
- `media` (the field itself, not `has_media`) is read directly by
  `app/ingestion/pipeline.py::_ingest_media` (`post.media`, line 235) — exactly
  the pattern the inline comment describes.
- `PostRepository` (`app/repositories/post_repository.py`) sets `model = Post`,
  upserts on `on_conflict="platform,platform_post_id"`, and adds
  `by_platform`, `by_author`, `posted_between` query methods.
- **`has_media`, however, is never read anywhere outside its own definition and
  `tests/unit/test_models.py`** (`test_post_has_media_false_by_default`,
  `test_post_has_media_true_with_media`, plus a round-trip test at line ~92) —
  the pipeline and Gradio UI both check `post.media` truthiness/length
  directly where they need to (e.g. `pipeline.py:235`:
  `if not persisted_post_id or not post.media:`), never `post.has_media`. Same
  "defined, tested, unused in production" pattern as
  `is_deleted`/`has_transcript`/`is_reply`/`engagement_rate`.

---

## 13. `app/models/db/__init__.py`

The file is **empty** (0 bytes) — it exists solely so `app/models/db` is
importable as a regular Python package (`app.models.db.orm`). It re-exports
nothing, so every consumer imports directly from `app.models.db.orm` (confirmed
by grep: both call sites, `app/database/sql_engine.py` and
`scripts/print_schema.py`, use `from app.models.db.orm import KNOWN_TABLES`,
never `from app.models.db import ...`).

---

## 14. `app/models/db/orm.py`

```python
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
    ARRAY, BigInteger, Boolean, Column, ForeignKey, Integer, MetaData, Numeric, Table, Text,
)
from sqlalchemy.dialects.postgresql import JSONB, TIMESTAMP, UUID
```
This module is architecturally distinct from every file above: it uses
**SQLAlchemy Core** (`Table`/`Column`/`MetaData`), not Pydantic — because its
job is describing the Postgres schema for a safety check, not
validating/serializing application data. `Column`/`ForeignKey`/etc. are
SQLAlchemy's declarative building blocks; `JSONB`/`TIMESTAMP`/`UUID` are
Postgres-specific column types from the `postgresql` dialect module (matching
the real column types used in `migrations/*.sql`).

```python
metadata = MetaData()
```
The registry every `Table(...)` call below implicitly attaches itself to (each
`Table(name, metadata, ...)` call registers `name` into `metadata.tables`).

### The tables
Twenty `Table` objects are declared: `platforms`, `authors`, `channels`,
`posts`, `videos`, `media`, `hashtags`, `post_hashtags`, `mentions`,
`comments`, `engagement`, `users`, `conversations`, `messages`, `query_logs`,
`assistant_logs`, `scrape_jobs`, `documents`, `embeddings` — one per table
created across `migrations/0001` through `migrations/0004`. Each declares its
columns, primary key, `ForeignKey(...)` references, `unique=True`/`nullable=`
flags, mirroring (approximately — see the drift note below) the corresponding
`create table` statement.

```python
KNOWN_TABLES: frozenset[str] = frozenset(metadata.tables.keys())
```
The **only** thing actually consumed elsewhere. A frozen set of the 20 table
names, built once at import time from whatever tables were registered above.

**Where used:** `app/database/sql_engine.py:21` imports `KNOWN_TABLES` and uses
it in `validate_sql_tables()` (line 92: `unknown = referenced - KNOWN_TABLES -
cte_aliases`) — part of `assert_sql_is_safe()`, the guard the AI assistant's
generated SQL must pass before `execute_readonly_sql()` will run it (see
`app/ai/sql_generator.py`). `scripts/print_schema.py:20` also imports
`KNOWN_TABLES` purely to print the list of table names as an audit tool
("Useful when auditing that `migrations/*.sql` and the AI's schema grounding
haven't drifted apart," per its own docstring).

**Important finding — the individual `Table` objects (columns, `ForeignKey`
constraints, etc.) are never queried, compiled, or otherwise used at
runtime.** Every consumer (`sql_engine.py`, `print_schema.py`) only ever reads
`KNOWN_TABLES`, which needs nothing but the *keys* of `metadata.tables` — the
column-level detail in each `Table(...)` call (types, `nullable`, `unique`,
foreign keys) is written out in full but has no functional consumer anywhere
in `app/` or `scripts/`. This makes the column definitions here effectively
**documentation with SQLAlchemy syntax**, not executable schema.

**Schema-drift finding:** because the column-level detail has no functional
consumer, it has drifted out of sync with the real schema
(`migrations/0002_core_content_tables.sql`) in several places — a real
discrepancy worth knowing about if anyone *does* start relying on this file for
column-level accuracy:
- `authors`: missing `profile_url`, `avatar_url`, `location`, `external_url`
  (all present in the migration and in `Author`).
- `channels`: missing `description`, `country` (present in the migration and
  in `Channel`).
- `posts`: missing `location` (present in the migration and in `Post`).
- `videos`: missing `thumbnail_url`, `video_url`, `language` (present in the
  migration and in `Video`).
- `comments`: missing `language`, `hashtags`, `mentions` (present in the
  migration and in `Comment`).
- `media`: missing `thumbnail_url`, `duration_seconds`, `file_size_bytes`,
  `alt_text` (present in the migration and in `Media` — see section 10's note).

None of this causes a functional bug today (the SQL safety check only cares
about table names being in `KNOWN_TABLES`, never column names), but it means
`orm.py` should **not** be treated as an authoritative, column-accurate mirror
of the live schema despite its module docstring's framing — the Pydantic
models in `app/models/pydantic/` and the raw `migrations/*.sql` files are the
actual sources of truth for column shape.

---

## Cross-file patterns worth remembering

1. **Mixin composition.** Every domain model in `app/models/pydantic/` is built
   from the same four building blocks in `base.py`
   (`IdentifiedMixin`/`CreatedAtMixin`/`TimestampMixin`/`SoftDeleteMixin`) plus
   `BaseSchema`. The mixins chosen for each class exactly track which real
   Postgres columns that table has (no `updated_at` mixin on append-only
   tables, no `SoftDeleteMixin` on tables without `deleted_at`) — this is
   directly checkable against `migrations/0001`-`0004`.

2. **`dedup_key` is the load-bearing computed field.** `Author`, `Channel`,
   `Video`, `Comment`, `Post` all expose a `dedup_key` computed property of the
   shape `f"{platform}:{platform_native_id}"`. `app/normalization/common.py`'s
   `dedupe_by_key`/`get_or_register` and `app/ingestion/pipeline.py`'s
   `_build_id_map` all key off this property to collapse duplicate scrapes and
   to translate a client-generated `.id` into the DB-persisted `.id` after a
   bulk upsert (Postgres doesn't guarantee upsert response order matches input
   order, so matching by list position would be unsafe).

3. **The `model_copy(update={...})` FK-remap pattern.** Every model with a
   foreign key to another domain model (`Channel.author_id`, `Post.author_id`,
   `Video.channel_id`/`post_id`, `Comment.post_id`/`author_id`/
   `parent_comment_id`, `Media.post_id`, `Mention.post_id`/`comment_id`,
   `Engagement.post_id`) is first built with the *locally generated* parent id
   (from `IdentifiedMixin`), then `app/ingestion/pipeline.py::_run` calls
   `.model_copy(update={"parent_field": id_map.get(local_id, local_id)})` to
   swap in the *persisted* parent id once the parent has actually been
   upserted. Comments are the one two-pass case: `parent_comment_id` is zeroed
   out before the first upsert (line 193, `"parent_comment_id": None`) because
   a comment's parent might be in the *same* batch and not yet have a
   persisted id, then `_relink_comment_parents` does a second `update()` call
   per reply once every comment in the batch has been assigned its persisted
   id.

4. **`@computed_field` vs `Field(exclude=True)`.** Every derived, non-persisted
   property in this package (`dedup_key`, `has_media`, `has_transcript`,
   `is_reply`, `total_participants`, `display_title`, `total_engagement`,
   `engagement_rate`) is a `@computed_field` `@property`, which `model_dump()`
   includes by default — so `BaseRepository._serialize` has to actively strip
   every name in `model.model_computed_fields` before writing to Supabase.
   `Post.media` is the one exception: it's a real, mutable field (not derived),
   so it uses `Field(exclude=True)` instead, an unconditional exclusion at the
   field level rather than the dynamic computed-field stripping.

5. **The "defined but unused in production" pattern recurs often.** Besides
   `EmbeddingDocument` (documented separately), this same category shows up
   repeatedly in this group of files: `SoftDeleteMixin.is_deleted`,
   `Video.has_transcript`, `Comment.is_reply`, `Comment.Reply` (the whole
   class), `Thread` (the whole class), `Post.has_media`,
   `Engagement.engagement_rate`, and (short of "unused," but close)
   `Platform`/`PlatformRepository.get_by_name`. In every case the pipeline or
   UI code re-derives the same boolean/value inline from the raw field
   (`post.media`, `comment.parent_comment_id`, `video.transcript`) rather than
   calling the computed property, and the property's only real consumer is
   `tests/unit/test_models.py`. Two enum members follow the same pattern at
   the value level: `EmbeddingSourceType.CAPTION`/`DESCRIPTION` are declared
   but never assigned by `_generate_embeddings`.

6. **Repository-local models mirror this package's conventions without being
   part of it.** `Document`/`EmbeddingRow` (`app/repositories/embedding_repository.py`)
   and `ScrapeJob` (`app/repositories/scrape_job_repository.py`) all subclass
   `BaseSchema`/mixins/enums imported directly from `app.models.pydantic.base`
   and `.enums`, but are not re-exported through
   `app/models/pydantic/__init__.py` — they exist next to the repository that
   owns them because they mirror a DB table shape more precisely than a
   "spec-shaped" model in this package would (see `docs/embedding_model_explained.md`
   section 2 for the fullest example of this, with `EmbeddingDocument` vs.
   `Document`/`EmbeddingRow`).
