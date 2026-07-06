# `app/ai/` and `app/prompts/` — line-by-line and how it fits the app

This document covers five files that together implement the app's AI assistant:
natural-language chat, natural-language-to-SQL generation, and the prompt
templates both consume. Each file gets its own section: every import, class,
function/method (arguments + return types), and constant is explained, along
with *why* it's written that way — then traced to its real call sites via grep
(file:line), not guesswork.

Reading order matters: `app/prompts/templates.py` and `app/prompts/__init__.py`
define the raw text; `app/ai/sql_generator.py` and `app/ai/assistant.py` consume
it; `app/ai/__init__.py` is the public re-export surface for the whole package.
This doc still follows the assignment's requested file order (`app/ai/__init__.py`
first), but forward-references the prompt files freely since they're small and
foundational.

---

## 1. `app/ai/__init__.py`

```python
from app.ai.assistant import Assistant
from app.ai.sql_generator import SQLGenerator

__all__ = ["Assistant", "SQLGenerator"]
```

A pure re-export module — no logic of its own. It exists so callers elsewhere in
the app can write `from app.ai import Assistant` (or `SQLGenerator`) instead of
reaching into the submodule paths `app.ai.assistant` / `app.ai.sql_generator`
directly. `__all__` is the explicit whitelist of names exported by
`from app.ai import *`, and doubles as documentation of the package's public
surface for anyone skimming the package.

**Where this re-export is actually used:** grepping the whole `app/` tree shows
every real caller imports from the *submodule* paths instead of the package
root:

- `app/services/chat_service.py:8` — `from app.ai.assistant import Assistant`
- `app/ai/assistant.py:14` — `from app.ai.sql_generator import SQLGenerator`

No file in `app/` actually does `from app.ai import Assistant` or
`from app.ai import SQLGenerator`. So `app/ai/__init__.py`'s re-export is
currently unused by the app itself — it functions as the intended public API
(useful for external callers, notebooks, or future code) rather than a load-
bearing import path today. This mirrors the pattern already documented for
`EmbeddingDocument` in `docs/embedding_model_explained.md`: the "front door" of
the package isn't necessarily the door the app itself walks through.

---

## 2. `app/ai/assistant.py`

### Module docstring and imports

```python
"""The AI assistant: answers questions using retrieved records and/or
generated SQL, always grounded (never hallucinated), always cited, and
always logged (conversation, message, query log, assistant log) — see
success criteria #11 and #13.
"""

from __future__ import annotations
```
States the module's four non-negotiable properties up front (grounded, cited,
logged, dual-sourced), which the rest of the file exists to enforce. `from
__future__ import annotations` postpones evaluation of type hints (PEP 563),
letting the file use modern union syntax (`str | None`) and forward references
without runtime cost or `NameError` risk — consistent with every other model
file in the app (see `embedding_model_explained.md` §1 for the same rationale).

```python
import time
from dataclasses import asdict
```
- `time` — used for `time.perf_counter()`, a monotonic clock unaffected by
  system clock adjustments, appropriate for measuring elapsed wall time of a
  single request rather than `time.time()` (which is affected by NTP
  adjustments and DST).
- `asdict` — converts a `dataclass` instance (specifically `RetrievalFilters`,
  which is a `@dataclass(slots=True)` per `app/retrieval/models.py:10-25`) into
  a plain `dict` for JSON-safe logging.

```python
from openai import AsyncOpenAI
```
The async OpenAI SDK client — used because the assistant's `ask()` method is
itself `async def` (it awaits DB repository calls and retrieval), so a
synchronous OpenAI client would block the event loop during the chat
completion call.

```python
from app.ai.sql_generator import SQLGenerator
from app.config import get_settings
from app.logging import get_logger
from app.models.pydantic import AssistantLog, ChatMessage, Conversation, QueryLog
from app.models.pydantic.enums import MessageRole
from app.prompts import (
    ASSISTANT_SYSTEM_PROMPT,
    CONVERSATION_MEMORY_PROMPT,
    CROSS_PLATFORM_COMPARISON_PROMPT,
    SENTIMENT_ANALYSIS_PROMPT,
    SUMMARIZATION_PROMPT,
    TREND_ANALYSIS_PROMPT,
)
from app.repositories.conversation_repository import ConversationRepository
from app.repositories.message_repository import MessageRepository
from app.repositories.query_log_repository import AssistantLogRepository, QueryLogRepository
from app.retrieval import RetrievalFilters, RetrievalService
from app.utils.exceptions import AssistantError
```
Note it imports **six of the seven** prompt templates from `app.prompts` — the
only one it does *not* import is `SQL_GENERATION_PROMPT`, because that one is
used exclusively by `SQLGenerator` (see §3). The Pydantic models
(`AssistantLog`, `ChatMessage`, `Conversation`, `QueryLog`) are the four things
the module docstring promises get "always logged." `MessageRole` is the
`StrEnum` (`USER`/`ASSISTANT`/`SYSTEM`, defined in
`app/models/pydantic/enums.py:49-52`) used to tag which role wrote a
`ChatMessage`. The four repositories are the persistence layer for
conversations, messages, query logs, and assistant logs respectively.
`RetrievalFilters`/`RetrievalService` come from `app/retrieval/__init__.py`,
which re-exports them from `app/retrieval/models.py` and
`app/retrieval/service.py`. `AssistantError` is the module's own exception,
defined in `app/utils/exceptions.py:76-77` as the base class for AI-assistant
failures (it's the parent of `SQLGenerationError`/`UnsafeSQLError` used in
`sql_generator.py`).

```python
logger = get_logger(__name__)
```
Standard structured logger, one per module, named after the module path.

### Module-level constants

```python
_MAX_CONTEXT_CHARS_PER_RECORD = 500
_MAX_RETRIEVED_RECORDS = 8
_MAX_SQL_ROWS_IN_CONTEXT = 20
_MAX_HISTORY_TURNS = 6
```
All four are prefixed `_` (module-private) and exist purely as **prompt token
budget controls** — they bound how much text gets stuffed into the LLM's
context window:
- `_MAX_CONTEXT_CHARS_PER_RECORD` — truncates each retrieved record's content
  to 500 characters before it's added to the prompt (see `result.content[:...]`
  usage below) — prevents one long post from crowding out the other retrieved
  records.
- `_MAX_RETRIEVED_RECORDS` — caps `hybrid_search(...)` to 8 results, i.e. the
  assistant never reasons over more than 8 retrieved snippets per turn.
- `_MAX_SQL_ROWS_IN_CONTEXT` — caps how many rows of a (possibly much larger)
  SQL result set are actually pasted into the prompt as `preview`.
- `_MAX_HISTORY_TURNS` — caps how many of the most recent prior messages are
  included as conversation memory, so a long-running conversation doesn't grow
  the prompt unboundedly turn after turn.

```python
_INTENT_KEYWORDS: dict[str, tuple[str, ...]] = {
    "trend": ("trend", "trending", "over time", "time-series", "timeseries"),
    "sentiment": ("sentiment", "feel about", "opinion", "controversial"),
    "comparison": ("compare", "comparison", " vs ", "versus", "cross-platform"),
    "summary": ("summarize", "summary", "sum up", "tl;dr"),
}
```
A comment directly above this dict (lines 40-45) explains the design choice
explicitly: this is "cheap keyword-based intent detection," deliberately
**not** a second LLM call, because classifying intent with an LLM would double
the latency and cost of every single question just to pick which specialized
prompt template to append — and keyword matching is judged "genuinely this
easy to approximate" for these four intents. Each key maps to a tuple of
substrings checked via a case-insensitive `in` scan (see `_style_prompt_for`
below).

### `_style_prompt_for(question, context_text) -> str | None`

```python
def _style_prompt_for(question: str, context_text: str) -> str | None:
    """Return a specialized analysis-style system prompt for `question`, if
    its wording matches one of the supported analysis intents, else `None`.
    """
    lowered = question.lower()
    if any(k in lowered for k in _INTENT_KEYWORDS["trend"]):
        return TREND_ANALYSIS_PROMPT.format(data=context_text)
    if any(k in lowered for k in _INTENT_KEYWORDS["sentiment"]):
        return SENTIMENT_ANALYSIS_PROMPT.format(content=context_text)
    if any(k in lowered for k in _INTENT_KEYWORDS["comparison"]):
        return CROSS_PLATFORM_COMPARISON_PROMPT.format(platform_data=context_text)
    if any(k in lowered for k in _INTENT_KEYWORDS["summary"]):
        return SUMMARIZATION_PROMPT.format(content=context_text)
    return None
```
- **Arguments**: `question` (the raw user question, mixed case) and
  `context_text` (the already-assembled retrieval/SQL context string built
  later in `ask()` — but this function is *called* after that string is built,
  so the specialized prompt can embed the same context the generic system
  prompt sees).
- **Return type**: `str | None` — `None` means "no specialized style applies,"
  and the caller (`ask()`) simply skips adding an extra system message in that
  case.
- **Why order matters**: the four `if` branches are checked in a fixed
  priority order (trend → sentiment → comparison → summary) and return on the
  *first* match — so a question matching keywords from two categories (e.g.
  "compare the trend in sentiment...") deterministically gets the first
  matching template, not an error or a blend. This is a conscious simplicity
  trade-off: only one specialized prompt is ever layered on, never several.
- Each branch calls `.format(...)` on the corresponding template constant from
  `app/prompts/templates.py`, injecting `context_text` under the placeholder
  name specific to that template (`data`, `content`, `platform_data`,
  `content` again) — see §5 for why the placeholder names differ per template.

### `_filters_to_json(filters) -> dict`

```python
def _filters_to_json(filters: RetrievalFilters | None) -> dict:
    """JSON-safe dict of applied filters for `query_logs.filters_applied` —
    `RetrievalFilters` may carry `datetime` values that need isoformatting.
    """
    if filters is None:
        return {}
    return {
        key: value.isoformat() if hasattr(value, "isoformat") else value
        for key, value in asdict(filters).items()
        if value is not None
    }
```
- **Argument**: `filters` — the `RetrievalFilters` dataclass (`platform`,
  `author_username`, `hashtag`, `date_from`, `date_to`, `min_likes`,
  `content_types`; see `app/retrieval/models.py:10-25`), or `None` if the
  caller applied no filters.
- **Return type**: `dict` — written directly into `QueryLog.filters_applied`
  (a `jsonb` column per `SCHEMA_DESCRIPTION`, `app/database/schema_metadata.py:34-35`).
- **Why it exists**: `asdict(filters)` alone would leave `date_from`/`date_to`
  as raw `datetime` objects, which are not JSON-serializable as-is when the
  Supabase/PostgREST client serializes the row for insert. `.isoformat()` (via
  `hasattr(value, "isoformat")`, a duck-typed check rather than `isinstance
  (value, datetime)` — cheaper and doesn't require importing `datetime` here)
  converts any datetime-like field to a string.
- The trailing `if value is not None` filter drops unset fields entirely
  rather than storing them as JSON `null` — keeps the logged filter dict
  minimal (only the filters a user's question actually implied).

### `class Assistant`

#### `__init__`

```python
def __init__(
    self,
    retrieval: RetrievalService | None = None,
    sql_generator: SQLGenerator | None = None,
    client: AsyncOpenAI | None = None,
    model: str | None = None,
    conversation_repo: ConversationRepository | None = None,
    message_repo: MessageRepository | None = None,
    query_log_repo: QueryLogRepository | None = None,
    assistant_log_repo: AssistantLogRepository | None = None,
) -> None:
    settings = get_settings()
    self.retrieval = retrieval or RetrievalService()
    self.sql_generator = sql_generator or SQLGenerator()
    self.client = client or AsyncOpenAI(api_key=settings.openai_api_key.get_secret_value())
    self.model = model or settings.openai_chat_model
    self.conversation_repo = conversation_repo or ConversationRepository()
    self.message_repo = message_repo or MessageRepository()
    self.query_log_repo = query_log_repo or QueryLogRepository()
    self.assistant_log_repo = assistant_log_repo or AssistantLogRepository()
```
Every one of the eight constructor parameters is optional and defaults to a
real, production-ready instance if omitted — textbook dependency injection.
This exists purely for **testability**: unit tests can construct an
`Assistant` with mocked `RetrievalService`/`SQLGenerator`/repos/client without
needing a live OpenAI key or Supabase connection, while production code (see
`ChatService.__init__` in `app/services/chat_service.py:21`, which does
`self.assistant = assistant or Assistant()`) gets fully-wired real
dependencies for free by calling `Assistant()` with no arguments.

- `settings = get_settings()` (`app/config/__init__.py` re-exporting
  `app/config/settings.py`) — a cached settings singleton (env-driven config).
- `self.client = client or AsyncOpenAI(api_key=settings.openai_api_key.get_secret_value())`
  — `openai_api_key` is a Pydantic `SecretStr` (`app/config/settings.py:50`),
  so `.get_secret_value()` is required to extract the plain string; `SecretStr`
  exists so the raw key never appears accidentally in a `repr()`/log line of
  the settings object.
- `self.model = model or settings.openai_chat_model` — defaults to
  `"gpt-4o-mini"` (`app/config/settings.py:51`), overridable per-instance.

#### `async def ask(self, question, *, conversation_id=None, filters=None) -> ChatMessage`

This is the assistant's single public entry point. Walking through it in
execution order:

**1. Timing and conversation bootstrap**
```python
start = time.perf_counter()

if conversation_id is None:
    conversation = await self.conversation_repo.create(Conversation(title=question[:60]))
    conversation_id = str(conversation.id)
```
If the caller doesn't pass an existing `conversation_id`, a new `Conversation`
row is created lazily, titled with the first 60 characters of the question
(a cheap default title — `Conversation.display_title`, a `@computed_field` in
`app/models/pydantic/conversation.py:27-30`, falls back to `"New conversation"`
only when `title` is `None`, not empty). This lazy-creation pattern is exactly
what `app/gradio/chat_tab.py:73-75`'s `_new_chat()` docstring describes:
*""New chat" just resets local UI state — `ChatService.ask` lazily creates the
actual conversation row on the first question.*"

**2. History retrieval**
```python
history = await self.message_repo.by_conversation(conversation_id, limit=20)
history_text = "\n".join(f"{m.role}: {m.content}" for m in history[-_MAX_HISTORY_TURNS:])
```
Fetches up to 20 stored messages, then further truncates to the last
`_MAX_HISTORY_TURNS` (6) when building the text blob actually sent to the LLM
— the extra headroom (20 fetched vs. 6 used) is not explained in-code but is
consistent with allowing future features (e.g. showing full history in a UI
sidebar) without a second query.

**3. Persist the user's turn immediately**
```python
await self.message_repo.create(
    ChatMessage(conversation_id=conversation_id, role=MessageRole.USER, content=question)
)
```
The user's message is saved *before* any SQL/retrieval/LLM call — so even if
everything downstream fails, the question itself is never lost.

**4. SQL generation (best-effort, non-fatal)**
```python
try:
    sql_generated, rows = await self.sql_generator.generate_and_execute(
        question, history_text
    )
    if rows:
        preview = rows[:_MAX_SQL_ROWS_IN_CONTEXT]
        context_parts.append(f"SQL query results ({len(rows)} total rows):\n{preview}")
        sources.append(f"sql:{sql_generated[:80]}")
except Exception as exc:  # noqa: BLE001 - SQL is a nice-to-have, not required
    logger.warning(
        "SQL generation/execution unavailable, using retrieval only", error=str(exc)
    )
```
Calls `SQLGenerator.generate_and_execute` (§3). The broad `except Exception`
(explicitly annotated `# noqa: BLE001` to silence the linter's "blind except"
warning) is deliberate: if SQL generation fails validation
(`UnsafeSQLError`), fails to execute, or the LLM call itself fails, the
assistant **falls back to retrieval-only** rather than surfacing an error to
the user — the inline comment states the design intent plainly: *"SQL is a
nice-to-have, not required."* The `sources.append(f"sql:{sql_generated[:80]}")`
line records (truncated to 80 chars) which SQL produced this turn's data, for
citation purposes.

**5. Hybrid retrieval (also best-effort, non-fatal)**
```python
try:
    retrieved = await self.retrieval.hybrid_search(
        question, filters, limit=_MAX_RETRIEVED_RECORDS
    )
except Exception as exc:  # noqa: BLE001 - never let retrieval failure kill the turn
    logger.warning("Retrieval unavailable", error=str(exc))
    retrieved = []

for result in retrieved:
    snippet = result.content[:_MAX_CONTEXT_CHARS_PER_RECORD]
    context_parts.append(
        f"[{result.platform}/{result.source_type}/{result.source_id[:8]}] {snippet}"
    )
    sources.append(f"{result.source_type}:{result.source_id}")
```
Same fallback philosophy as SQL: if `RetrievalService.hybrid_search` (§4/§ below)
raises for any reason, retrieval degrades to an empty list rather than
crashing the turn. Each surviving result is truncated to 500 chars
(`_MAX_CONTEXT_CHARS_PER_RECORD`) and formatted with a bracketed tag
`[platform/source_type/first-8-chars-of-id]` — this bracketed tag is exactly
the citation format `ASSISTANT_SYSTEM_PROMPT` (§5) instructs the model to use
inline in its answer ("cite it inline as (platform, author/channel, short
id)"). Both SQL and retrieval failing independently is tolerated: the
assistant can still answer using whichever source succeeded, or fall through
to "(no relevant records found)" if both fail.

**6. Building the LLM prompt**
```python
context_text = (
    "\n\n".join(context_parts) if context_parts else "(no relevant records found)"
)

messages: list[dict[str, str]] = [{"role": "system", "content": ASSISTANT_SYSTEM_PROMPT}]
if history_text:
    messages.append(
        {
            "role": "system",
            "content": CONVERSATION_MEMORY_PROMPT.format(history=history_text),
        }
    )
style_prompt = _style_prompt_for(question, context_text)
if style_prompt:
    messages.append({"role": "system", "content": style_prompt})
user_turn = f"Context:\n{context_text}\n\nQuestion: {question}"
messages.append({"role": "user", "content": user_turn})
```
The final OpenAI chat message list is built layer by layer, each layer
optional except the base system prompt and the final user turn:
1. `ASSISTANT_SYSTEM_PROMPT` — always present, the grounding/citation/honesty
   contract (§5).
2. `CONVERSATION_MEMORY_PROMPT` — only added if there *is* prior history,
   formatted with the truncated `history_text` from step 2.
3. A specialized analysis-style prompt from `_style_prompt_for` — only added
   if the question's keywords matched one of the four supported intents.
4. The actual user turn — context (SQL rows + retrieved snippets, or the
   "no relevant records" placeholder) followed by the literal question. This
   single-string interleaving (rather than putting context in yet another
   system message) is what lets the model see "here's what's true" and "here's
   what's asked" as one coherent unit.

**7. The chat completion call**
```python
try:
    response = await self.client.chat.completions.create(
        model=self.model, messages=messages, temperature=0.3  # type: ignore[arg-type]
    )
except Exception as exc:
    raise AssistantError(f"Chat completion failed: {exc}") from exc
```
Unlike SQL generation and retrieval, a failure *here* is **not** swallowed —
it's re-raised as `AssistantError` (`app/utils/exceptions.py:76-77`), because
if the chat completion itself fails there is no answer to give the user at
all; SQL/retrieval are optional inputs, but the completion call is the one
step with no fallback. `temperature=0.3` is a deliberately low-but-nonzero
value: low enough to keep answers factual/consistent (contrast with
`SQLGenerator`'s `temperature=0`, which needs to be *fully* deterministic
since it's generating executable code), but not fully deterministic since
this is prose generation where some variation is acceptable. The `# type:
ignore[arg-type]` suppresses a static-typing complaint about the `messages`
list's dict shape not exactly matching the OpenAI SDK's stricter
`ChatCompletionMessageParam` union type.

**8. Extracting the answer and usage stats**
```python
answer = (response.choices[0].message.content or "").strip()
usage = response.usage
prompt_tokens = getattr(usage, "prompt_tokens", None)
completion_tokens = getattr(usage, "completion_tokens", None)
execution_time_ms = (time.perf_counter() - start) * 1000
```
`content or ""` guards against the SDK returning `None` (e.g. a
content-filtered or empty response) before `.strip()` is called — calling
`.strip()` on `None` would raise. `getattr(usage, ..., None)` guards against
`response.usage` itself being `None` in edge cases (e.g. certain streaming or
error-adjacent responses). `execution_time_ms` measures the *entire* `ask()`
call from the very first line, not just the completion call — so it captures
retrieval + SQL + LLM latency together, matching what gets stored in both
`ChatMessage.execution_time_ms` and `AssistantLog.execution_time_ms`.

**9. Persisting the assistant's turn**
```python
assistant_message = await self.message_repo.create(
    ChatMessage(
        conversation_id=conversation_id,
        role=MessageRole.ASSISTANT,
        content=answer,
        sources=sources,
        sql_generated=sql_generated,
        model_used=self.model,
        execution_time_ms=execution_time_ms,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
    )
)
```
Every field of `ChatMessage` (`app/models/pydantic/conversation.py:33-42`) that
exists specifically to support auditing/debugging a chat turn is populated
here: which sources were cited, what SQL (if any) ran, which model answered,
how long it took, and token counts.

**10. The two audit logs**
```python
await self.query_log_repo.create(
    QueryLog(
        conversation_id=conversation_id,
        query_text=question,
        retrieved_document_ids=[s for s in sources if not s.startswith("sql:")],
        filters_applied=_filters_to_json(filters),
        latency_ms=execution_time_ms,
    )
)
await self.assistant_log_repo.create(
    AssistantLog(
        conversation_id=conversation_id,
        message_id=str(assistant_message.id),
        prompt_used=user_turn[:4000],
        sql_generated=sql_generated,
        model_used=self.model,
        execution_time_ms=execution_time_ms,
        token_usage={
            "prompt_tokens": prompt_tokens or 0,
            "completion_tokens": completion_tokens or 0,
        },
    )
)

return assistant_message
```
Two distinct, purpose-separated logs (per `QueryLog`'s own docstring at
`app/models/pydantic/conversation.py:45-49`, "kept separate so query analytics
survive even if the chat UI/message schema changes"):
- `QueryLog` — the *user-facing query analytics* record: what was asked, which
  non-SQL sources (retrieval hits) backed it (`sources` filtered to exclude the
  `"sql:..."` entries), which filters were applied, and latency.
- `AssistantLog` — the *generation-internals* record: the exact prompt text
  sent (truncated to 4000 chars to bound row size), any generated SQL, model,
  timing, and token usage as a small JSON dict.

Finally, `ask()` returns the persisted `assistant_message` (a `ChatMessage`)
— exactly what `ChatService.ask` (see §2's "callers" below) passes straight
through to the Gradio UI.

### Where `Assistant` is actually used

Grepping the whole `app/` tree for `Assistant(` / `from app.ai.assistant`:

- **`app/services/chat_service.py:8`** — `from app.ai.assistant import Assistant`
- **`app/services/chat_service.py:21`** — `self.assistant = assistant or Assistant()`,
  inside `ChatService.__init__`. `ChatService` (its own module docstring:
  *"Thin orchestration layer between the Gradio chat UI and `app.ai.Assistant`
  + the conversation repositories — the UI never touches the AI/retrieval/DB
  layers directly."*) is the **only** production caller of `Assistant`.
- **`app/services/chat_service.py:25-26`** —
  ```python
  async def ask(self, question: str, *, conversation_id: str | None = None) -> ChatMessage:
      return await self.assistant.ask(question, conversation_id=conversation_id)
  ```
  `ChatService.ask` is a near-transparent pass-through to `Assistant.ask` (it
  does not currently forward a `filters` argument through to the assistant —
  `RetrievalFilters` support exists in `Assistant.ask`/`RetrievalService` but
  isn't yet wired up from the Gradio layer).
- **`app/gradio/chat_tab.py:139`** — `reply = await _get_chat_service().ask(question, conversation_id=conversation_id)`
  inside the `_ask` async generator that backs the Gradio "Send"/Enter-key
  event. This is the actual end-user entry point: Gradio UI → `ChatService.ask`
  → `Assistant.ask` → (SQL generator + retrieval service + OpenAI) → `ChatMessage`
  back up the chain, rendered into the `gr.Chatbot` history.
- `app/gradio/chat_tab.py:32` documents *why* `ChatService()` construction is
  deferred to first use (`_get_chat_service()`) rather than at import time:
  constructing `Assistant()` eagerly builds an `AsyncOpenAI` client, which
  needs a configured API key — deferring it lets the Gradio Blocks graph still
  build in a bare dev checkout with no credentials configured.

No test files or other modules construct `Assistant` directly outside this
one production call chain (`chat_tab.py` → `chat_service.py` → `assistant.py`).

---

## 3. `app/ai/sql_generator.py`

### Module docstring and imports

```python
"""Natural-language -> SQL generation, grounded in `SCHEMA_DESCRIPTION` and
validated/executed through `app.database.sql_engine` before any row reaches
the assistant.
"""

from __future__ import annotations

import re

from openai import AsyncOpenAI

from app.config import get_settings
from app.database import SCHEMA_DESCRIPTION, assert_sql_is_safe, execute_readonly_sql
from app.logging import get_logger
from app.prompts import SQL_GENERATION_PROMPT
from app.utils.exceptions import SQLGenerationError, UnsafeSQLError

logger = get_logger(__name__)
```
The docstring states the file's entire safety contract in one sentence:
generated SQL is *validated and executed* through `app.database.sql_engine`
"before any row reaches the assistant" — i.e. this module never hands raw,
unchecked SQL text or rows back without going through the gate described in
§"Safety mechanism" below.

- `re` — used to build `_FENCE_RE`, stripping markdown code fences from the
  LLM's raw text output.
- `AsyncOpenAI` — same rationale as in `assistant.py`: async client for an
  async method.
- `SCHEMA_DESCRIPTION, assert_sql_is_safe, execute_readonly_sql` — all three
  imported from `app.database` (re-exported by `app/database/__init__.py:1-2`
  from `app/database/schema_metadata.py` and `app/database/sql_engine.py`
  respectively). This is the entire safety boundary: `SCHEMA_DESCRIPTION`
  grounds *generation* (tells the model what tables/columns exist so it's less
  likely to hallucinate), `assert_sql_is_safe`/`execute_readonly_sql` gate
  *execution*.
- `SQL_GENERATION_PROMPT` — the one prompt template from `app.prompts` this
  module uses (contrast with `assistant.py`, which imports the other six).
- `SQLGenerationError, UnsafeSQLError` — from `app/utils/exceptions.py:80-85`;
  `UnsafeSQLError` is a *subclass* of `SQLGenerationError`, which is itself a
  subclass of `AssistantError`. That inheritance chain is why
  `Assistant.ask`'s single `except Exception` around
  `sql_generator.generate_and_execute(...)` (§2, step 4) correctly catches
  both failure modes without needing to know the distinction — but the
  distinction still exists in the type hierarchy for callers (like tests or
  future stricter callers) that *do* want to tell "the LLM produced bad SQL/it
  failed to run" apart from "the LLM produced something actively unsafe."

### `_FENCE_RE` and `_strip_fences`

```python
_FENCE_RE = re.compile(r"^```(?:sql)?\s*|\s*```$", re.IGNORECASE | re.MULTILINE)


def _strip_fences(text: str) -> str:
    return _FENCE_RE.sub("", text).strip().rstrip(";")
```
- The regex matches an opening code fence at the start of the text — ` ``` `
  optionally followed by `sql` (case-insensitive), optionally followed by
  whitespace — **or** a closing ` ``` ` at the end. `re.MULTILINE` makes `^`/`$`
  match at line boundaries (not just string start/end), since the LLM's
  output could have the fence on its own line. This exists because
  `SQL_GENERATION_PROMPT` explicitly instructs "no markdown fences," but LLMs
  are known to add them anyway despite instructions — this is a defensive
  cleanup, not a trust that the instruction alone works.
- `.strip()` removes surrounding whitespace/newlines left after fence removal.
- `.rstrip(";")` removes a trailing semicolon — necessary because
  `sql_engine.assert_sql_is_safe` (§ below) explicitly checks `";" in
  normalized.rstrip(";")` to detect **multiple** statements; a single
  legitimately-terminated statement like `SELECT * FROM posts;` must have its
  own trailing `;` removed first, or that check would have nothing to
  distinguish "one statement with a trailing semicolon" from "two statements
  separated by a semicolon."

### `class SQLGenerator`

#### `__init__(self, client=None, model=None) -> None`

```python
def __init__(self, client: AsyncOpenAI | None = None, model: str | None = None) -> None:
    settings = get_settings()
    self.client = client or AsyncOpenAI(api_key=settings.openai_api_key.get_secret_value())
    self.model = model or settings.openai_chat_model
```
Same dependency-injection pattern as `Assistant.__init__`: both dependencies
optional, defaulting to real instances built from `get_settings()`. Notably,
`SQLGenerator` uses **the same model setting** (`settings.openai_chat_model`,
default `"gpt-4o-mini"`) as the conversational assistant — there's no separate
"SQL model" configured, so whichever chat model is configured is used for
both prose answers and SQL generation.

#### `async def generate(self, question, conversation_context="") -> str`

```python
async def generate(self, question: str, conversation_context: str = "") -> str:
    """Ask the LLM for a single read-only SQL statement answering `question`."""
    prompt = SQL_GENERATION_PROMPT.format(
        schema=SCHEMA_DESCRIPTION,
        conversation_context=conversation_context or "(none)",
        question=question,
    )
    try:
        response = await self.client.chat.completions.create(
            model=self.model,
            messages=[{"role": "user", "content": prompt}],
            temperature=0,
        )
    except Exception as exc:
        raise SQLGenerationError(f"SQL generation request failed: {exc}") from exc

    content = response.choices[0].message.content or ""
    return _strip_fences(content)
```
- **Arguments**: `question` (the natural-language question) and
  `conversation_context` (optional prior-turn history text, defaulting to
  empty string — substituted as the literal string `"(none)"` in the prompt
  when empty, so the template always has *something* human-readable there
  rather than a blank line).
- Builds the full prompt via `SQL_GENERATION_PROMPT.format(...)`, injecting the
  static `SCHEMA_DESCRIPTION` (§ below), the conversation context, and the
  question into the three named placeholders the template defines.
- A **single** user message is sent (no system message, unlike `Assistant.ask`)
  — the entire instruction set (rules, schema, question) lives in one
  formatted user-role string, since `SQL_GENERATION_PROMPT` is self-contained.
- `temperature=0` — fully deterministic sampling. This is a deliberate
  contrast with `Assistant.ask`'s `temperature=0.3`: SQL is executable code
  with a strict grammar, so variability is pure downside here (more likely to
  produce a subtly different, unvalidated query on retries), whereas prose
  benefits from a little variation.
- Any request-level failure (network, auth, rate limit, etc.) is wrapped into
  `SQLGenerationError` with the original exception chained (`from exc`), so a
  stack trace still shows the root cause.
- `content = response.choices[0].message.content or ""` — same `None`-guard
  pattern as `assistant.py`.
- Returns the fence-stripped SQL text, **not yet validated** — validation is
  `generate_and_execute`'s job, not `generate`'s. This split exists so callers
  that only want the *generated text* (e.g. a future "show me the SQL you'd
  run" preview feature, or a unit test asserting on generation) can call
  `generate` alone without needing a live/validate-capable DB connection.

#### `async def generate_and_execute(self, question, conversation_context="") -> tuple[str, list[dict]]`

```python
async def generate_and_execute(
    self, question: str, conversation_context: str = ""
) -> tuple[str, list[dict]]:
    """Generate SQL, validate it's safe, and execute it.

    Raises `UnsafeSQLError` if the generated statement fails validation —
    callers (the assistant) should catch this and fall back to
    retrieval-only rather than surfacing it to the user directly.
    """
    sql = await self.generate(question, conversation_context)
    assert_sql_is_safe(sql)
    logger.info("Executing assistant-generated SQL", sql=sql, question=question)
    try:
        rows = execute_readonly_sql(sql)
    except UnsafeSQLError:
        raise
    except Exception as exc:
        raise SQLGenerationError(f"Generated SQL execution failed: {exc}") from exc
    return sql, rows
```
This is the method `Assistant.ask` actually calls (§2, step 4). Sequence:
1. `sql = await self.generate(...)` — get raw (fence-stripped) SQL text.
2. `assert_sql_is_safe(sql)` — the safety gate (detailed below); raises
   `UnsafeSQLError` (uncaught here, so it propagates straight to the caller)
   if the statement fails any check.
3. Logs the SQL *before* executing it (structured log with both `sql` and
   `question` fields) — an audit trail independent of the `AssistantLog`
   DB row, useful for ops/debugging even if the DB write path itself is what's
   broken.
4. `execute_readonly_sql(sql)` — actually runs the query. Wrapped so that a
   (redundant, since `execute_readonly_sql` itself calls `assert_sql_is_safe`
   again internally — see §"Safety mechanism") `UnsafeSQLError` is
   re-raised as-is (`except UnsafeSQLError: raise`), while any *other*
   exception (a real DB error — bad join, timeout, connection failure) gets
   wrapped into `SQLGenerationError` instead, preserving the original
   traceback via `from exc`.
5. Returns a `(sql, rows)` tuple: the exact SQL text that ran, and the
   resulting rows as `list[dict]`. This tuple shape is exactly what
   `Assistant.ask` destructures as `sql_generated, rows = await
   self.sql_generator.generate_and_execute(...)`.

The docstring's explicit callout — *"callers (the assistant) should catch this
and fall back to retrieval-only rather than surfacing it to the user
directly"* — is precisely what `Assistant.ask`'s broad `except Exception`
around this call implements (§2, step 4).

### The safety/validation mechanism (why arbitrary/destructive SQL cannot run)

This is layered across **three independent checks**, all living in
`app/database/sql_engine.py`, invoked from `sql_generator.py` only via
`assert_sql_is_safe`/`execute_readonly_sql`:

```python
_ALLOWED_SQL_PREFIXES = ("select", "with")
_FORBIDDEN_KEYWORDS = (
    "insert", "update", "delete", "drop", "alter", "truncate",
    "grant", "revoke", "create", "--", ";--",
)
_TABLE_REF_RE = re.compile(r"\b(?:from|join)\s+([a-zA-Z_][a-zA-Z0-9_]*)", re.IGNORECASE)
_CTE_ALIAS_RE = re.compile(r"(?:with|,)\s+([a-zA-Z_][a-zA-Z0-9_]*)\s+as\s*\(", re.IGNORECASE)
_FORBIDDEN_KEYWORD_RE = re.compile(
    r"\b(" + "|".join(re.escape(k) for k in _FORBIDDEN_KEYWORDS) + r")\b", re.IGNORECASE
)


def assert_sql_is_safe(sql: str) -> None:
    """Guard against anything but a single read-only SELECT/CTE statement."""
    normalized = sql.strip().lower()
    if not normalized.startswith(_ALLOWED_SQL_PREFIXES):
        raise UnsafeSQLError(f"Only SELECT/WITH statements are permitted, got: {sql[:80]!r}")
    if ";" in normalized.rstrip(";"):
        raise UnsafeSQLError("Multiple statements are not permitted.")
    match = _FORBIDDEN_KEYWORD_RE.search(normalized)
    if match:
        raise UnsafeSQLError(f"Disallowed keyword {match.group(1)!r} found in generated SQL.")
    validate_sql_tables(sql)


def validate_sql_tables(sql: str) -> None:
    """Reject SQL that references tables outside the known schema."""
    referenced = {m.group(1).lower() for m in _TABLE_REF_RE.finditer(sql)}
    cte_aliases = {m.group(1).lower() for m in _CTE_ALIAS_RE.finditer(sql)}
    unknown = referenced - KNOWN_TABLES - cte_aliases
    if unknown:
        raise UnsafeSQLError(f"Generated SQL references unknown table(s): {sorted(unknown)}")


def execute_readonly_sql(sql: str, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """Validate and execute a single read-only SQL statement, returning rows
    as a list of dicts.
    """
    assert_sql_is_safe(sql)
    engine = get_engine()
    logger.info("Executing AI-generated SQL", sql=sql)
    with engine.connect() as conn:
        result = conn.execute(text(sql), params or {})
        rows: list[dict[str, Any]] = [dict(row._mapping) for row in result]
    return rows
```

Breaking down each layer:

1. **Statement-prefix allowlist.** `normalized.startswith(("select", "with"))`
   — the *only* two statement types ever allowed through. This isn't a
   blocklist of dangerous keywords; it's a strict allowlist of the entire
   first token of the statement, so anything that isn't a `SELECT` or a `WITH`
   (CTE, which still must eventually resolve to a `SELECT`) is rejected
   outright, no matter what it says.

2. **Single-statement enforcement.** `";" in normalized.rstrip(";")` — after
   stripping one trailing semicolon (the legitimate terminator, already
   stripped once by `_strip_fences` in `sql_generator.py` but re-checked here
   defensively), *any* remaining semicolon means there's a second statement
   stacked after the first (classic SQL-injection-via-stacked-queries
   pattern: `SELECT 1; DROP TABLE users;`). This blocks statement chaining
   even if the first statement alone would have passed the prefix check.

3. **Forbidden-keyword scan, whole-word only.** `_FORBIDDEN_KEYWORD_RE` uses
   `\b(...)\b` word boundaries around each of `insert/update/delete/drop/
   alter/truncate/grant/revoke/create/--/;--`. The code comments explain two
   subtleties explicitly:
   - Whole-word matching specifically avoids false positives on legitimate
     column names like `created_at`/`updated_at`/`deleted_at`, which contain
     "create"/"update"/"delete" as *substrings* — a naive `in` check would
     reject nearly every query in this schema.
   - `--` and `;--` are included as forbidden "keywords" to catch SQL-comment-
     based injection tricks (e.g. appending `-- ` to comment out the rest of a
     query and smuggle in different logic), even though they're punctuation,
     not real keywords.

4. **Table-reference allowlisting against `KNOWN_TABLES`.**
   `validate_sql_tables` extracts every identifier following `FROM`/`JOIN`
   via `_TABLE_REF_RE`, separately extracts CTE aliases (identifiers before
   `AS (` following `WITH` or a comma — i.e. `WITH recent AS (...)`) via
   `_CTE_ALIAS_RE` so a query's own named subqueries aren't mistaken for
   invalid table references, and rejects the SQL if `referenced - KNOWN_TABLES
   - cte_aliases` is non-empty. `KNOWN_TABLES` (`app/models/db/orm.py:302`,
   `frozenset[str] = frozenset(metadata.tables.keys())`) is derived directly
   from the SQLAlchemy `Table` objects declared in that same file — so it's
   always in sync with the real schema as new tables are added to
   `models/db/orm.py`, and that file's own module docstring
   (`app/models/db/orm.py:5-12`) states its purpose explicitly: *"the AI SQL
   generator can validate that generated SQL only references real
   tables/columns... without needing a live database round-trip"* and
   *"`KNOWN_TABLES` is the whitelist `assert_sql_is_safe` uses to reject
   hallucinated table references in AI-generated SQL."* This catches the
   failure mode where an LLM invents a plausible-but-nonexistent table name.
   The docstring on `validate_sql_tables` is honest about its limits too: it's
   "a cheap regex scan (not a full SQL parser)" — it catches the *common*
   hallucination case, and anything more exotic that slips past it (e.g. a
   subquery structure the regex doesn't recognize) still gets caught by
   Postgres itself raising a real error when the query actually executes,
   since nothing here bypasses the database's own referential integrity.

5. **Defense in depth: the check runs twice.** `execute_readonly_sql` calls
   `assert_sql_is_safe(sql)` itself, on top of `generate_and_execute` already
   having called it once. So even a future caller that calls
   `execute_readonly_sql` directly (bypassing `SQLGenerator` entirely) still
   gets the full validation — the safety boundary lives at the execution
   function, not merely at the generator that happens to be the only current
   caller.

6. **Read-only connection semantics.** `execute_readonly_sql` uses a
   SQLAlchemy `Engine` (`get_engine()`, `@lru_cache`d so the engine/connection
   pool is built once) that connects to `settings.supabase_db_url` — a direct
   Postgres connection, deliberately separate from the Supabase/PostgREST
   client the repositories use for normal CRUD. The module docstring
   (`app/database/sql_engine.py:1-9`) explains why: *"the AI assistant needs
   to run arbitrary SELECT queries the repositories don't have methods for,
   and SQLAlchemy gives us a straightforward `text()` execution path plus
   statement-level safety checks that would be awkward to bolt onto
   PostgREST."* Rows are converted via `dict(row._mapping)` per row — plain
   dicts, not ORM objects, since the whole point is running ad hoc queries
   with no fixed result shape.

**Net effect**: even a fully compromised or adversarially-prompted LLM cannot
use this path to mutate, drop, or alter data, escalate privileges, or run
multiple statements — it can only ever run a single `SELECT`/`WITH` statement
against tables that already exist in the real schema. The worst it can do is
return an over-broad or wrong *read*.

### Where `SQLGenerator` is actually used

- **`app/ai/assistant.py:14`** — `from app.ai.sql_generator import SQLGenerator`
- **`app/ai/assistant.py:97`** — `self.sql_generator = sql_generator or SQLGenerator()`
  in `Assistant.__init__`.
- **`app/ai/assistant.py:134-136`** —
  ```python
  sql_generated, rows = await self.sql_generator.generate_and_execute(
      question, history_text
  )
  ```
  the sole call site of `generate_and_execute` in the whole app, inside
  `Assistant.ask` (§2, step 4).

No other module in `app/` imports or constructs `SQLGenerator` directly — it
is reached exclusively through `Assistant`, which is itself reached
exclusively through `ChatService` → the Gradio chat tab (see §2's call chain).

---

## 4. Supporting pieces referenced above (for completeness)

Two dependencies that `assistant.py`/`sql_generator.py` lean on heavily are
worth summarizing here since the assignment asks specifically where they fit:

- **`app/retrieval/service.py` (`RetrievalService`)** — constructed by
  `Assistant.__init__` (`self.retrieval = retrieval or RetrievalService()`,
  `app/ai/assistant.py:96`) and called exactly once per turn, in
  `Assistant.ask`, as `await self.retrieval.hybrid_search(question, filters,
  limit=_MAX_RETRIEVED_RECORDS)` (`app/ai/assistant.py:147-149`).
  `RetrievalService`'s own module docstring
  (`app/retrieval/service.py:1-7`) states its role in the architecture
  directly: *"This is the one place the AI assistant goes to fetch 'relevant
  records' — it never queries `documents`/`embeddings`/`posts` directly."*
  `hybrid_search` (`app/retrieval/service.py:120-166`) runs `keyword_search`
  (Postgres full-text via `.text_search(...)`, `app/retrieval/service.py:48-94`)
  and `semantic_search` (pgvector cosine similarity via the `match_embeddings`
  RPC, `app/retrieval/service.py:96-118`) concurrently with `asyncio.gather`,
  merges results keyed by `(source_type, source_id)` with weights
  `_KEYWORD_WEIGHT = 0.4` / `_SEMANTIC_WEIGHT = 0.6`, applies any
  `RetrievalFilters` (platform/author/hashtag/date/likes/content-type, in
  `_apply_filters`), sorts by combined score, and returns the top `limit`
  `RetrievalResult`s — which is exactly the list `Assistant.ask` iterates to
  build `context_parts`/`sources` (§2, step 5).

- **`app/database/sql_engine.py`** — used exclusively by `sql_generator.py`
  (`from app.database import SCHEMA_DESCRIPTION, assert_sql_is_safe,
  execute_readonly_sql`, `app/ai/sql_generator.py:13`), as detailed in the
  Safety Mechanism section above. `get_engine()` is the only other function in
  this module and is called internally by `execute_readonly_sql`, never
  imported into `sql_generator.py` directly.

- **`app/database/schema_metadata.py`** (`SCHEMA_DESCRIPTION`) — a static,
  hand-maintained multi-line string describing every table, its columns, its
  foreign keys, and (crucially) prose "Notes" at the bottom calling out two
  real gotchas the model would otherwise get wrong: (1) only six tables have a
  `deleted_at` column (`authors`, `channels`, `posts`, `videos`, `comments`,
  `conversations`) — applying that filter to any other table is a
  column-does-not-exist SQL error; and (2) every table's `platform` column is
  the lowercase platform *name* stored as plain text, not a foreign key to
  `platforms.id` (a `uuid`) — joining/comparing it against `platforms.id`
  would be a type error. The module docstring
  (`app/database/schema_metadata.py:1-8`) explains why this is a static string
  rather than introspected live from the DB: *"prompt construction never
  depends on a live DB connection and stays fast/deterministic; update it
  alongside migrations/ when the schema changes."* This constant is imported
  by `sql_generator.py` (`from app.database import SCHEMA_DESCRIPTION`,
  `app/ai/sql_generator.py:13`) and injected into `SQL_GENERATION_PROMPT`'s
  `{schema}` placeholder in `SQLGenerator.generate` (§3).
  `app/database/schema_metadata.py:1-2`'s own docstring cross-references
  `sql_generator.py` by name, confirming the intended coupling.

---

## 5. `app/prompts/__init__.py`

```python
from app.prompts.templates import (
    ASSISTANT_SYSTEM_PROMPT,
    CONVERSATION_MEMORY_PROMPT,
    CROSS_PLATFORM_COMPARISON_PROMPT,
    SENTIMENT_ANALYSIS_PROMPT,
    SQL_GENERATION_PROMPT,
    SUMMARIZATION_PROMPT,
    TREND_ANALYSIS_PROMPT,
)

__all__ = [
    "ASSISTANT_SYSTEM_PROMPT",
    "CONVERSATION_MEMORY_PROMPT",
    "CROSS_PLATFORM_COMPARISON_PROMPT",
    "SENTIMENT_ANALYSIS_PROMPT",
    "SQL_GENERATION_PROMPT",
    "SUMMARIZATION_PROMPT",
    "TREND_ANALYSIS_PROMPT",
]
```
Same pattern as `app/ai/__init__.py`: a pure re-export module with no logic,
flattening `app.prompts.templates`'s seven constants onto the `app.prompts`
package namespace so callers can write `from app.prompts import
ASSISTANT_SYSTEM_PROMPT` (which is exactly how both `assistant.py:19-26` and
`sql_generator.py:15` import them) instead of reaching into
`app.prompts.templates` directly. Unlike `app/ai/__init__.py` (whose re-export
turned out to be unused by real callers), **this re-export is the one actually
used everywhere** — every consumer in the app imports from `app.prompts`, not
`app.prompts.templates`, confirmed by grep: no file under `app/` imports
`from app.prompts.templates import ...` directly.

---

## 6. `app/prompts/templates.py`

### Module docstring

```python
"""Reusable prompt templates for the AI assistant.

Kept as plain `.format()`-style string templates (not a templating engine)
because every prompt here is short, has a fixed set of named placeholders,
and pulling in Jinja2/langchain-prompts for this would be exactly the
"unnecessary framework" the spec warns against.
"""

from __future__ import annotations
```
Explicitly justifies the low-tech approach: seven `str` constants using
Python's built-in `"{name}".format(...)` substitution, rather than a
templating library. The stated reasoning — short prompts, fixed placeholders,
avoiding an "unnecessary framework" — means every template below is just a
triple-quoted string with `{placeholder}` markers, formatted via `.format()`
at the call site (never `f-string`s, since the values aren't known until the
caller has them).

### `SQL_GENERATION_PROMPT`

```python
SQL_GENERATION_PROMPT = """You are a PostgreSQL expert generating a single read-only query against a \
social media analytics database.

Schema:
{schema}

Rules:
- Output ONLY the SQL statement, no explanation, no markdown fences.
- Only SELECT or WITH statements are allowed. Never write INSERT/UPDATE/DELETE/DDL.
- Always filter `deleted_at is null` on tables that have that column, unless the question \
explicitly asks about deleted/removed content.
- Prefer explicit column lists over `select *`.
- Use ILIKE for case-insensitive text matching.
- Limit results to a reasonable number (default 20) unless the question asks for a count/aggregate.

Conversation context (may be empty):
{conversation_context}

Question: {question}

SQL:"""
```
Three named placeholders: `{schema}` (filled with `SCHEMA_DESCRIPTION`),
`{conversation_context}` (prior turns, or the literal string `"(none)"`), and
`{question}` (the user's raw question) — matching exactly the three keyword
arguments passed in `SQLGenerator.generate`'s `.format(schema=..., 
conversation_context=..., question=...)` call (`app/ai/sql_generator.py:35-39`).
Note this is the **first line of defense**, not the only one: the "Rules"
section is a *prompt-level instruction* telling the model to behave (only
SELECT/WITH, no DDL/DML, filter soft-deletes, prefer explicit columns, use
`ILIKE`, cap result size) — but as `sql_engine.py`'s code comments make clear,
none of this is trusted on its own; every one of these rules (statement type,
row limits aside) is also *mechanically enforced* after generation by
`assert_sql_is_safe`/`validate_sql_tables`, precisely because an LLM
instruction is a suggestion, not a guarantee. This is the **only** template
used by `sql_generator.py` — every other template below is used exclusively
by `assistant.py`.

### `SUMMARIZATION_PROMPT`, `TREND_ANALYSIS_PROMPT`, `SENTIMENT_ANALYSIS_PROMPT`, `CROSS_PLATFORM_COMPARISON_PROMPT`

```python
SUMMARIZATION_PROMPT = """Summarize the following social media content in a concise, neutral tone. \
Focus on the main themes, sentiment, and any notable engagement patterns. Cite specific posts by \
their platform and author when relevant.

Content:
{content}

Summary:"""

TREND_ANALYSIS_PROMPT = """Analyze the following data for trends (topics, hashtags, engagement over \
time, or platform differences). Call out the top 3-5 patterns with supporting numbers.

Data:
{data}

Trend analysis:"""

SENTIMENT_ANALYSIS_PROMPT = """Classify the overall sentiment (positive/negative/neutral/mixed) of \
the following content, and briefly justify the classification with 1-2 supporting quotes.

Content:
{content}

Sentiment analysis:"""

CROSS_PLATFORM_COMPARISON_PROMPT = """Compare engagement and discussion patterns for the same topic \
across platforms, using the data below. Highlight differences in volume, sentiment, and audience \
reaction.

Platform data:
{platform_data}

Comparison:"""
```
These four are the "analysis-style" templates consumed exclusively by
`_style_prompt_for` in `assistant.py` (§2), one per supported intent keyword
bucket. Each has exactly **one** placeholder, but the placeholder's *name*
differs per template (`{content}` for summarization and sentiment, `{data}`
for trend, `{platform_data}` for comparison) even though all four are fed the
same value (`context_text`) by the caller — this is purely a readability
choice per-template (the name reflects what that specific analysis is
conceptually operating over), not a functional difference, since
`_style_prompt_for` passes the identical `context_text` string to whichever
one matches, just under that template's own keyword:
```python
TREND_ANALYSIS_PROMPT.format(data=context_text)
SENTIMENT_ANALYSIS_PROMPT.format(content=context_text)
CROSS_PLATFORM_COMPARISON_PROMPT.format(platform_data=context_text)
SUMMARIZATION_PROMPT.format(content=context_text)
```
Each template ends its own instruction with a bare trailing label (`Summary:`,
`Trend analysis:`, `Sentiment analysis:`, `Comparison:`) — a common prompting
technique to prime the model to continue directly with the requested output
type rather than restating the question.

### `ASSISTANT_SYSTEM_PROMPT`

```python
ASSISTANT_SYSTEM_PROMPT = """You are a social media intelligence assistant. You answer questions \
using ONLY the retrieved records and SQL results provided to you in the user turn — never invent \
facts, numbers, or posts that are not present in that context. When you use a specific record, cite \
it inline as (platform, author/channel, short id). If the provided context does not contain enough \
information to answer, say so plainly instead of guessing. Keep answers concise and directly \
responsive to the question."""
```
No placeholders — a fixed system message, always the *first* message in
`Assistant.ask`'s `messages` list (`app/ai/assistant.py:165`). This is the
prompt-level implementation of the grounding/citation contract the module
docstring of `assistant.py` promises ("always grounded... always cited"): it
explicitly forbids inventing facts not present in the provided context,
mandates the exact citation format `(platform, author/channel, short id)`
(which lines up with the bracketed `[platform/source_type/id[:8]]` tags
`Assistant.ask` builds into `context_parts`, §2 step 5), and instructs the
model to admit insufficient context rather than guess — the prompt-level
half of "never hallucinated," complementing (not replacing) the mechanical
safety checks on the SQL side.

### `CONVERSATION_MEMORY_PROMPT`

```python
CONVERSATION_MEMORY_PROMPT = """Previous conversation turns (most recent last):
{history}

Use this history only to resolve references (e.g. "that post", "the second one") in the current \
question — do not repeat information from it unless it is directly relevant to a new answer."""
```
One placeholder, `{history}`, filled by `Assistant.ask` with the
newline-joined, `_MAX_HISTORY_TURNS`-truncated `history_text` string
(`app/ai/assistant.py:169-172`), and only added as a system message when
`history_text` is non-empty (`if history_text:` at `app/ai/assistant.py:166`).
Its instruction is narrowly scoped — resolve pronoun/reference ambiguity
("that post," "the second one"), not re-surface old information — which keeps
the assistant's answers focused on the *current* question rather than
re-summarizing prior turns unprompted.

### Summary: which template is used by which caller

| Template | Used by | How |
|---|---|---|
| `SQL_GENERATION_PROMPT` | `sql_generator.py` (`SQLGenerator.generate`) | Sole user-role prompt sent to the LLM for SQL generation |
| `ASSISTANT_SYSTEM_PROMPT` | `assistant.py` (`Assistant.ask`) | Always the first system message |
| `CONVERSATION_MEMORY_PROMPT` | `assistant.py` (`Assistant.ask`) | Added as a system message only when prior history exists |
| `TREND_ANALYSIS_PROMPT` | `assistant.py` (`_style_prompt_for`) | Added as a system message when the question matches "trend" keywords |
| `SENTIMENT_ANALYSIS_PROMPT` | `assistant.py` (`_style_prompt_for`) | Added when the question matches "sentiment" keywords |
| `CROSS_PLATFORM_COMPARISON_PROMPT` | `assistant.py` (`_style_prompt_for`) | Added when the question matches "comparison" keywords |
| `SUMMARIZATION_PROMPT` | `assistant.py` (`_style_prompt_for`) | Added when the question matches "summary" keywords |

`sql_generator.py` and `assistant.py` have **no overlap** in which templates
they consume — `SQL_GENERATION_PROMPT` is exclusively the SQL generator's, and
the other six are exclusively the assistant's.
