# `app/apify/*` and `app/normalization/*` — line-by-line and how it fits the app

This document covers the two packages that sit between "an Apify actor ran" and
"a `ScrapeResult` full of validated Pydantic models exists": `app/apify` (actor
orchestration + platform scraper classes) and `app/normalization` (raw-dict →
Pydantic mapping). It follows the same approach as
`docs/embedding_model_explained.md`: every import/class/function is explained,
then traced to its real call sites via grep across `app/`, `tests/`, and
`scripts/` — not assumed.

**Big picture up front**, because every file below is a piece of this one loop:

```
scripts/run_scrape.py  (CLI)
        |
        v
app/services/scrape_service.py  (ScrapeService)
        |
        |-- get_scraper(platform)              --> app/apify/__init__.py  (registry lookup)
        |-- scraper.scrape_{profile,posts,...} --> app/apify/<platform>/scraper.py
        |         |-- runner.run_and_fetch()    --> app/apify/base/client.py (ApifyActorRunner)
        |         |-- normalize_*(raw_item)     --> app/normalization/<platform>.py
        |         v  returns
        |     ScrapeResult                       --> app/apify/base/scraper.py
        v
app/ingestion/pipeline.py  (IngestionPipeline.ingest)
        |-- dedupe_by_key / get_or_register     --> app/normalization/common.py
        |-- NORMALIZERS[post.platform].extract_engagement(post) --> app/normalization/__init__.py
        v
Supabase (via app/repositories/*) + app/embeddings (see embedding_model_explained.md)
```

---

# apify/base

## `app/apify/base/__init__.py`

```python
from app.apify.base.client import ApifyActorRunner, get_apify_client
from app.apify.base.scraper import BaseScraper, ScrapeResult

__all__ = ["ApifyActorRunner", "get_apify_client", "BaseScraper", "ScrapeResult"]
```

Pure re-export module. Its only job is to give `app/apify/__init__.py` (and
anything else) a single shallow import path (`from app.apify.base import ...`)
instead of reaching into `app.apify.base.client` / `app.apify.base.scraper`
directly. This is the standard "package `__init__.py` as a public-surface
allowlist" pattern used throughout the app (compare
`app/models/pydantic/__init__.py`).

- `ApifyActorRunner`, `get_apify_client` — defined in `client.py` (below).
- `BaseScraper`, `ScrapeResult` — defined in `scraper.py` (below).

**Where used:** `app/apify/__init__.py:15` imports all four names from here
(`from app.apify.base import ApifyActorRunner, BaseScraper, ScrapeResult,
get_apify_client`) and re-exports them again in its own `__all__` — so the
*outermost* public path a caller actually uses is `app.apify.ScrapeResult`,
`app.apify.BaseScraper`, etc. (or, for `ScrapeResult`, more commonly the
slightly deeper `app.apify.base.scraper.ScrapeResult`, which is what
`app/ingestion/pipeline.py:23` and every test file import directly).

---

## `app/apify/base/client.py`

The module docstring states its own rationale plainly: `apify-client` (the
official Apify SDK) is **synchronous**. The whole ingestion pipeline is
`asyncio`-based (`ScrapeService.scrape_many` runs many scrapes concurrently
under an `asyncio.Semaphore` — see `app/services/scrape_service.py:85-98`), so
every blocking SDK call in this file is pushed onto a worker thread via
`asyncio.to_thread` rather than the event loop.

### Imports

- `asyncio` — for `asyncio.to_thread`.
- `from datetime import timedelta` — Apify's `.call()` wants a run timeout as
  a `timedelta`, not raw seconds; this module accepts seconds (`timeout_secs:
  int | None`) for a friendlier call-site API and converts internally.
- `from functools import lru_cache` — memoizes `get_apify_client()` (see
  below) so the whole process shares one `ApifyClient`/HTTP connection pool.
- `from typing import Any` — used for `run_input: dict[str, Any]` and the
  dataset item type `dict[str, Any]`, since Apify actor inputs/outputs are
  untyped JSON until `app.normalization` gives them shape.
- `from apify_client import ApifyClient` — the actual Apify Python SDK client.
- `from apify_client._models import Run` — the SDK's return type for
  `actor(...).call(...)`; imported only for the type annotation `Run | None`
  on the inner `_run()` helper.
- `from app.config import get_settings` — pulls `apify_api_token` and (in
  callers) actor IDs from centralized app settings (`app/config/settings.py`)
  rather than hardcoding credentials/URLs.
- `from app.logging import get_logger` — the app's structured logger factory;
  every actor run logs a start/finish line with `actor_id`, `run_id`,
  `item_count`, etc., so a stuck or failed run is diagnosable from logs alone.
- `from app.utils.exceptions import ApifyRateLimitError, ApifyRunFailedError`
  — app-specific exception types (defined in `app/utils/exceptions.py:28-36`,
  both subclasses of `ScraperError` → `AppError`) so callers up the stack can
  catch "an Apify problem" without knowing SDK internals.
- `from app.utils.retry import with_retry` — a decorator factory
  (`app/utils/retry.py:36-50`) wrapping Tenacity's `retry()` with the app's
  standard exponential-backoff-with-jitter policy (`reraise=True`, so the
  *original* exception surfaces after retries are exhausted, not Tenacity's
  own `RetryError`).

### Module-level constant

```python
_FAILURE_STATUSES = {"FAILED", "ABORTED", "TIMED-OUT"}
```
The set of terminal Apify run statuses that mean "the run *finished* but not
successfully." `ApifyClient.actor(...).call()` already blocks until the run
reaches *some* terminal state (it doesn't return early on success vs.
failure), so this set is what `run_and_fetch` checks afterward to decide
whether to raise.

### `get_apify_client() -> ApifyClient`

```python
@lru_cache
def get_apify_client() -> ApifyClient:
    settings = get_settings()
    return ApifyClient(settings.apify_api_token.get_secret_value())
```
- `@lru_cache` (no `maxsize` arg → unbounded, but only ever called with zero
  arguments so it caches exactly one value) — makes this a process-wide
  singleton constructor, mirroring the same pattern used for Supabase/OpenAI
  clients elsewhere in the app. Avoids re-parsing settings and re-establishing
  a client (and its underlying connection pool) on every scraper instantiation.
- `settings.apify_api_token` is a Pydantic `SecretStr` (`app/config/settings.py:34`,
  defaulting to `SecretStr("")`), so `.get_secret_value()` is required to get
  the plain string `ApifyClient(token: str)` needs — `SecretStr` exists so the
  token never appears unmasked in `repr()`/logs by accident.

**Where used:** `ApifyActorRunner.__init__` (below) calls it as the default
client when no client is injected. Also referenced directly in
`tests/unit/test_apify_scrapers.py` per the grep in this doc's investigation
(tests patch/bypass it rather than hitting the real Apify API).

### `ApifyActorRunner`

```python
class ApifyActorRunner:
    def __init__(self, client: ApifyClient | None = None) -> None:
        self._client = client or get_apify_client()
```
The class docstring explains the design choice directly: keeping this as a
small stateful class (holding `self._client`) rather than free functions
means a test can construct `ApifyActorRunner(client=fake_client)` and inject a
fake, instead of monkeypatching a module-level global. Every platform scraper
(`InstagramScraper`, `TwitterScraper`, `YouTubeScraper`) takes an optional
`runner: ApifyActorRunner | None` in its own `__init__` (inherited from
`BaseScraper.__init__`, see `scraper.py` below) for exactly this reason — see
`tests/unit/test_apify_scrapers.py:110-123` constructing scrapers with a fake
runner that "records every `run_and_fetch` call."

#### `run_and_fetch(actor_id, run_input, *, memory_mbytes=None, timeout_secs=None) -> list[dict[str, Any]]`

```python
@with_retry(exceptions=(ApifyRateLimitError,), max_attempts=4, min_wait=2.0, max_wait=30.0)
async def run_and_fetch(self, actor_id, run_input, *, memory_mbytes=None, timeout_secs=None):
```
This is the **single seam** every platform scraper calls through to actually
talk to Apify — the docstring calls it "run an actor, wait for it, fetch the
dataset," and centralizing it here means all platform scrapers share identical
retry/rate-limit/failure handling instead of reimplementing it per platform.

Arguments:
- `actor_id: str` — Apify actor slug, e.g. `"apify/instagram-post-scraper"`.
  Always passed by callers from `get_settings().apify_<platform>_<x>_actor`
  (see `app/config/settings.py:35-42`) rather than hardcoded in scraper code,
  so actor versions/replacements are a config change, not a code change.
- `run_input: dict[str, Any]` — the actor-specific JSON input object. Its
  shape is entirely actor-dependent; this is why every platform scraper's
  docstrings spend so much time explaining *which* keys their actor expects.
- `memory_mbytes: int | None` — optional override of the actor run's memory
  allocation; passed straight through to `ApifyClient.actor(...).call()`.
- `timeout_secs: int | None` — converted to a `timedelta` only if not `None`
  (`timedelta(seconds=timeout_secs) if timeout_secs else None`), because the
  SDK's `run_timeout` parameter expects `timedelta | None`.

Decorator: `@with_retry(exceptions=(ApifyRateLimitError,), max_attempts=4,
min_wait=2.0, max_wait=30.0)` — retries **only** on `ApifyRateLimitError` (HTTP
429), up to 4 attempts, exponential backoff with jitter between 2s and 30s.
Any other exception (`ApifyRunFailedError`, network errors, etc.) propagates
immediately — retrying a run that failed for a non-rate-limit reason (bad
input, actor bug) would just waste quota repeating the same failure.

Body, step by step:
1. `_run()` — a synchronous inner closure (needed because
   `asyncio.to_thread` requires a plain callable, not a coroutine) that calls
   `self._client.actor(actor_id).call(run_input=..., memory_mbytes=...,
   run_timeout=...)`. Wrapped in `try/except Exception` because the SDK
   raises its own `ApifyApiError` (not imported/typed here — caught generically)
   on HTTP failures:
   - If `exc.status_code == 429` (read via `getattr(exc, "status_code",
     None)` defensively, since not every exception has that attribute) →
     raises `ApifyRateLimitError` (which `@with_retry` catches and retries).
   - Otherwise → raises `ApifyRunFailedError` wrapping the original exception
     (`from exc`, preserving the traceback chain), tagged with
     `context={"actor_id": actor_id}` (the app's `AppError` base class takes
     a `context` dict for structured error metadata — see
     `app/utils/exceptions.py:14-22`).
2. `run = await asyncio.to_thread(_run)` — actually dispatches the blocking
   call off the event loop. Logs "Starting Apify actor run" *before* this
   (line 84), so a hung actor run is visible in logs even before it resolves.
3. `if run is None: raise ApifyRunFailedError(...)` — defensive guard; the SDK
   contract says `.call()` shouldn't return `None`, but the code doesn't trust
   that silently.
4. `if run.status in _FAILURE_STATUSES: raise ApifyRunFailedError(...)` — the
   run *completed* (no exception) but ended in `FAILED`/`ABORTED`/`TIMED-OUT`.
   Context includes `run_id` and `status` for debugging via the Apify console.
5. `dataset_id = run.default_dataset_id` then a second `asyncio.to_thread`
   call (`_fetch_items`) does `list(self._client.dataset(dataset_id).iterate_items())`
   — pulls every item from the actor's default output dataset into memory as
   plain dicts. `list(...)` around a generator/iterator because
   `iterate_items()` is itself a paginating SDK generator; converting to a
   list up front means callers get one concrete collection rather than having
   to manage pagination themselves.
6. Logs "Apify actor run finished" with `item_count=len(items)`, then returns
   the raw list of dicts — deliberately **not** normalized here; normalization
   is `app.normalization`'s job (see below), keeping this client one layer
   below any domain concept.

**Where used (grepped):** Every platform scraper method calls
`self.runner.run_and_fetch(...)` exactly once per Apify actor invocation:
- `app/apify/instagram/scraper.py:39,54,86,131` (profile/post/comment/hashtag actors)
- `app/apify/twitter/scraper.py:55,66,99,132,156` (all methods funnel through the one `apify_twitter_scraper_actor`)
- `app/apify/youtube/scraper.py:69,93,129,169` (video listing actor twice, transcript actor, comment actor)

`ApifyActorRunner` itself is constructed directly (no injected runner) inside
`BaseScraper.__init__` (`app/apify/base/scraper.py:64`,
`self.runner = runner or ApifyActorRunner()`), and with an injected fake in
`tests/unit/test_apify_scrapers.py` (per its own docstring: "a fake runner
... that records every `run_and_fetch` call and returns [canned items]").

---

## `app/apify/base/scraper.py`

The module docstring frames this file's purpose as the extensibility seam of
the whole scraping layer: adding a new platform means writing one
`BaseScraper` subclass; nothing in `app/ingestion`, `app/services`, or the AI
assistant needs to change. It also states the key invariant: **every method
returns a `ScrapeResult`, never raw dicts** — so callers never branch on
platform — and methods a platform doesn't support raise `NotImplementedError`
with a clear message instead of silently returning empty results (which would
be indistinguishable from "ran but found nothing").

### Imports

- `from abc import ABC, abstractmethod` — `BaseScraper` is an actual abstract
  base class (not just a docstring convention); instantiating it directly
  without implementing the abstract methods raises `TypeError` at
  instantiation time, catching a missed-override bug immediately rather than
  at first call.
- `from dataclasses import dataclass, field` — `ScrapeResult` is a plain
  `@dataclass`, not a Pydantic model. It doesn't need validation (its list
  items are *already* validated `Post`/`Author`/etc. Pydantic models by the
  time they're wrapped) or JSON (de)serialization — it's a purely in-process
  transport container, so a dataclass is the lighter-weight, more appropriate
  tool than another Pydantic model.
- `from app.apify.base.client import ApifyActorRunner` — used as the type of
  `BaseScraper.runner` and its constructor's default.
- `from app.models.pydantic import Author, Channel, Comment, Media, Post,
  Video` — every domain type a scrape can possibly produce; these are the six
  list fields of `ScrapeResult`.

### `ScrapeResult` (`@dataclass(slots=True)`)

```python
@dataclass(slots=True)
class ScrapeResult:
    posts: list[Post] = field(default_factory=list)
    authors: list[Author] = field(default_factory=list)
    comments: list[Comment] = field(default_factory=list)
    media: list[Media] = field(default_factory=list)
    channels: list[Channel] = field(default_factory=list)
    videos: list[Video] = field(default_factory=list)
    raw_item_count: int = 0
```
- `slots=True` — no `__dict__` per instance; a reasonable micro-optimization
  since these are created once per scrape call and often merged/copied.
- Every list field uses `field(default_factory=list)` (not `= []`) — the
  standard dataclass-safe way to give a mutable default without every
  instance sharing the *same* list object.
- **Why one container with six lists instead of six separate return values or
  five separate calls**, per the class docstring: it keeps
  `app/ingestion/pipeline.py`'s "validate → normalize → dedupe → persist" loop
  (see `IngestionPipeline._run`) working over **one object shape** regardless
  of which scrape method or platform produced it. `scrape_profile` on
  Instagram only ever populates `authors`; `scrape_posts` on YouTube populates
  `authors`, `channels`, `posts`, *and* `videos` — but `IngestionPipeline._run`
  doesn't need to know or care which; it just reads `result.posts`,
  `result.authors`, etc. and each is empty-by-default if unused.
- `raw_item_count: int = 0` — the count of *raw* Apify dataset items this
  result was built from, independent of how many of those became valid
  `Post`/`Author`/etc. (some raw items are skipped as malformed — see each
  platform scraper's `except Exception: logger.warning("Skipping malformed
  ...")` blocks). Lets a caller compute a "how many raw items were dropped"
  signal for observability without the pipeline needing to inspect model
  lists.

#### `merge(self, other: ScrapeResult) -> ScrapeResult`
Returns a **new** `ScrapeResult` (does not mutate `self` or `other`) whose
every list is the concatenation `[*self.x, *other.x]` and whose
`raw_item_count` is the sum. Grepping the whole `app/`/`tests/` tree for
`.merge(` (and for `ScrapeResult(` more generally) shows this method has
**no current call site** anywhere in `app/` or `tests/` — it is defined but
not (yet) wired into `IngestionPipeline` or `ScrapeService`, both of which
currently call `pipeline.ingest(result, ...)` once per single scrape result
rather than merging several results into one before ingesting. It exists as
forward-looking API — e.g. for a future "scrape N profiles then ingest as one
batch" flow — but is presently unused. (Contrast with the `EmbeddingDocument`
situation in `docs/embedding_model_explained.md`: this isn't dead-by-mistake,
it's a small combinator kept ready for a batching use case the codebase
hasn't built yet.)

### `BaseScraper(ABC)`

```python
class BaseScraper(ABC):
    platform: str

    def __init__(self, runner: ApifyActorRunner | None = None) -> None:
        self.runner = runner or ApifyActorRunner()
```
- `platform: str` — a class-level annotation with no default; every concrete
  subclass sets it as a class attribute (e.g. `platform = "instagram"`). It's
  a plain string rather than `PlatformName` here specifically so scraper code
  can do simple string interpolation/logging (`f"{self.platform} does not
  support..."`) without an enum import in this base module — the *registry*
  (`app/apify/__init__.py`) is what maps the real `PlatformName` enum to the
  class.
- `__init__(runner=None)` — dependency injection point: production code
  (`ScrapeService`, `get_scraper`) constructs scrapers with no runner, so each
  gets its own `ApifyActorRunner()` (which itself defaults to the cached
  singleton `get_apify_client()`); tests construct scrapers with a fake
  runner to avoid network calls (see `tests/unit/test_apify_scrapers.py`).

Abstract methods (must be implemented by every subclass, `TypeError` at
instantiation otherwise):
- `async scrape_profile(self, identifier: str) -> ScrapeResult` — one
  profile/channel's metadata, no posts.
- `async scrape_posts(self, identifier: str, *, limit: int = 50) ->
  ScrapeResult` — recent posts/videos/tweets for a profile/channel.
- `async scrape_comments(self, post_url_or_id: str, *, limit: int = 100) ->
  ScrapeResult` — comments (and replies) for one post/video/tweet.

Concrete-with-default methods (subclasses may override, but get a sane
default if the platform doesn't support the concept):
- `async scrape_hashtag(self, hashtag: str, *, limit: int = 50) ->
  ScrapeResult` — default body: `raise NotImplementedError(f"{self.platform}
  does not support hashtag search")`.
- `async scrape_keyword(self, keyword: str, *, limit: int = 50) ->
  ScrapeResult` — same pattern, `"does not support keyword search"`.

These two are **not** `@abstractmethod` precisely because not every platform
supports them (YouTube has neither, via these actors — see
`app/apify/youtube/scraper.py`'s module docstring, which explicitly says the
base class's `NotImplementedError` default is correct and intentionally not
overridden there). Making them abstract would force every subclass to write a
boilerplate "not supported" override; leaving them concrete-with-a-default
means only platforms that *do* support the concept (Instagram, Twitter) need
to write any code at all.

**Where used (grepped):**
- Subclassed by `InstagramScraper` (`app/apify/instagram/scraper.py:28`),
  `TwitterScraper` (`app/apify/twitter/scraper.py:39`), `YouTubeScraper`
  (`app/apify/youtube/scraper.py:52`), and a `FakeScraper` test double in
  `tests/integration/test_services.py:29-49` that implements all five methods
  returning empty `ScrapeResult()`s (used to unit-test `ScrapeService` without
  touching Apify or a scraper subclass).
- `ScrapeResult` is imported and type-annotated on `IngestionPipeline.ingest`
  and `IngestionPipeline._run` (`app/ingestion/pipeline.py:23,120,142`) — this
  is the class referenced in the assignment as "understand `ScrapeResult`
  thoroughly — it's referenced by `app/ingestion/pipeline.py`." Concretely,
  `_run` reads `result.posts`, `result.authors`, `result.channels`,
  `result.videos`, `result.comments` (lines 143-208) to drive the
  dedupe→remap→persist sequence; `result.media` is populated by
  `normalize_post` embedding `Media` objects inside each `Post.media` list
  rather than as a separate top-level list the pipeline reads directly (media
  is instead pulled from `post.media` in `IngestionPipeline._ingest_media`).
- `tests/integration/test_ingestion_pipeline.py:234-264` builds a full
  `ScrapeResult` fixture (`_build_scrape_result()`) to drive an
  end-to-end-in-memory pipeline test, and line 370 builds a deliberately
  malformed one (`ScrapeResult(authors=[SimpleNamespace(username="broken")])`)
  to test that `dedupe_by_key` failures are isolated per-entity rather than
  aborting the whole `_run`.

---

# apify/instagram

## `app/apify/instagram/__init__.py`

```python
from app.apify.instagram.scraper import InstagramScraper

__all__ = ["InstagramScraper"]
```
Trivial re-export. Its real purpose is **import-time side effect**: importing
this package runs `scraper.py`'s module body, which executes the
`@register_scraper(PlatformName.INSTAGRAM)` class decorator on
`InstagramScraper` — registering it in `app/apify/__init__.py`'s `_REGISTRY`
dict. `app/apify/__init__.py:47` imports `instagram` (this package) precisely
to trigger that registration; nothing in the codebase calls
`app.apify.instagram.InstagramScraper` directly by that shallow path — it's
always reached either via `app.apify.instagram.scraper.InstagramScraper`
(tests) or via `get_scraper(PlatformName.INSTAGRAM)` (production).

## `app/apify/instagram/scraper.py`

Module docstring: this file's job is mapping "Instagram-specific Apify actor
conventions (separate actors for profile/post/hashtag/comment scraping, each
with its own input shape and raw item schema)" onto the platform-agnostic
`BaseScraper` interface. It also flags directly that Apify's Instagram actor
family's input keys "vary... and change between actor versions" — an
important caveat for anyone maintaining this file after an actor upgrade.

### Imports
- `from app.apify import register_scraper` — the decorator that registers
  this class into the platform→scraper registry (see
  `app/apify/__init__.py` above). Note the import path: `app.apify`, not
  `app.apify.base` — `register_scraper` lives in the top-level
  `app/apify/__init__.py`, not in `base`.
- `from app.apify.base.scraper import BaseScraper, ScrapeResult` — parent
  class and return type.
- `from app.config import get_settings` — reads the actor IDs
  (`apify_instagram_profile_actor`, etc.) per call, so a config change (e.g.
  swapping to a different actor slug) takes effect without restarting/caching
  concerns tied to the scraper object itself.
- `from app.logging import get_logger` — module-level `logger`, used only for
  `logger.warning(..., exc_info=True)` when a raw item fails to normalize.
- `from app.models.pydantic import Author` — used as the value type of the
  local `authors_by_key: dict[str, Author]` dedup caches.
- `from app.models.pydantic.enums import PlatformName` — passed to
  `@register_scraper(PlatformName.INSTAGRAM)`.
- `from app.normalization import get_or_register` — the shared
  first-seen-wins author dedup helper (see `common.py` below).
- `from app.normalization.instagram import normalize_author, normalize_comment,
  normalize_post` — the three normalizer functions this scraper calls to turn
  raw Apify dicts into `Author`/`Comment`/`Post` models.

### `InstagramScraper` (`@register_scraper(PlatformName.INSTAGRAM)`)

`platform = "instagram"` (matches `BaseScraper.platform`'s contract).

#### `scrape_profile(self, identifier: str) -> ScrapeResult`
Calls `apify_instagram_profile_actor` (`"apify/instagram-profile-scraper"`)
with `run_input={"usernames": [identifier]}` — the docstring notes `usernames`
is that actor's standard input key. Takes only `items[:1]` (a profile actor
run for one username should return one item, but this defensively slices
rather than assuming) and maps it through `normalize_author`. Returns
`ScrapeResult(authors=authors, raw_item_count=len(items))` — note
`raw_item_count` is the *full* `len(items)` even though only the first item
was used, so the count still reflects what the actor actually returned.

#### `scrape_posts(self, identifier: str, *, limit: int = 50) -> ScrapeResult`
Calls `apify_instagram_post_actor` with
`{"username": [identifier], "resultsLimit": limit}`. The docstring explicitly
flags that `username` must be an **array** for this actor — "verified live
against `apify/instagram-post-scraper` — it rejects a bare string with 'Field
input.username must be array'" — i.e. this isn't a guess, it's a documented
finding from actually running the actor.

For each raw item: `get_or_register(authors_by_key, normalize_author(item),
lambda a: a.dedup_key)` gets (or creates-and-caches) the canonical `Author`
for that item's embedded owner info, then `normalize_post(item,
author_id=str(author.id))` builds the `Post`, stamped with that canonical
author's id. Wrapped in `try/except Exception: logger.warning("Skipping
malformed Instagram post item", exc_info=True); continue` — one bad item never
aborts the whole batch (`exc_info=True` logs the full traceback for
debugging while still continuing). Returns `ScrapeResult(posts=posts,
authors=list(authors_by_key.values()), raw_item_count=len(items))`.

#### `scrape_comments(self, post_url_or_id: str, *, limit: int = 100) -> ScrapeResult`
Calls `apify_instagram_comment_actor` with `{"postUrls": [post_url_or_id],
"resultsLimit": limit}`. Docstring notes the actor accepts either a full post
URL or a bare id/shortcode, and that `post_url_or_id` is passed straight
through as `post_id` on the `Comment` because the scraper "doesn't have the
post's real (DB) id here" — `app/ingestion/pipeline.py` remaps
`Comment.post_id` from this placeholder to the persisted post's real id
during ingestion (via `dedup_key`-matched `post_id_map`, see
`IngestionPipeline._run` lines 187-197).

For each top-level item: normalizes an author + comment, then iterates
`item.get("replies") or item.get("childComments") or []` (another
version-drift defensive `or`-chain) to normalize each reply as its **own**
`Comment` with `parent_id=str(comment.id)` — the parent's *client-generated*
id at normalization time (later remapped to the DB id by
`IngestionPipeline._relink_comment_parents`, a second pass specifically
because the parent's *persisted* id isn't known until after the parent itself
is upserted). Both the top-level normalize and each reply's normalize are
independently wrapped in `try/except` so one malformed reply doesn't drop the
whole comment thread.

#### `scrape_hashtag(self, hashtag: str, *, limit: int = 50) -> ScrapeResult`
Calls `apify_instagram_hashtag_actor` with `{"hashtags":
[hashtag.lstrip("#")], "resultsLimit": limit}` (strips a leading `#` since the
actor wants the bare tag). Docstring: "Each raw item is post-shaped, so this
normalizes the same way as `scrape_posts`" — and indeed the body is
structurally identical to `scrape_posts` (one author + one post per item),
just against a different actor/input.

**Where `InstagramScraper` is actually used (grepped):**
- `app/apify/instagram/__init__.py:1` — the import that triggers registration.
- `app/apify/__init__.py:47` — imports the `instagram` package (transitively
  runs the decorator).
- **Never constructed directly** in `app/` production code — the only way
  production code reaches an `InstagramScraper` instance is
  `get_scraper(PlatformName.INSTAGRAM)` / `get_scraper("instagram")` in
  `app/services/scrape_service.py:43,52,61,70,79` (every `ScrapeService`
  method calls `get_scraper(platform)` fresh, so a new scraper — and
  implicitly a fresh `ApifyActorRunner()` — is built per call).
- Directly instantiated in tests: `tests/unit/test_apify_scrapers.py:110`
  (`InstagramScraper()`, exercising the real default-runner path against the
  cached client — noted in a comment there that `ApifyActorRunner.__init__`
  only calls `get_apify_client()`, which is safe to construct without
  credentials because it's lazy) and `:119,156,178` (constructed with a fake
  injected `runner` to test `scrape_profile`/`scrape_posts`/`scrape_comments`
  behavior against canned data).
- End-to-end CLI path: `scripts/run_scrape.py` → `ScrapeService` →
  `get_scraper("instagram")` → `InstagramScraper`, e.g. `python
  scripts/run_scrape.py instagram posts nasa --limit 50` (usage example in the
  script's own docstring, `scripts/run_scrape.py:8`).

---

# apify/twitter

## `app/apify/twitter/__init__.py`

```python
from app.apify.twitter.scraper import TwitterScraper

__all__ = ["TwitterScraper"]
```
Same role as the Instagram `__init__.py`: re-export + import-time trigger for
`@register_scraper(PlatformName.TWITTER)`, imported by
`app/apify/__init__.py:47`.

## `app/apify/twitter/scraper.py`

Module docstring explains the structural difference from Instagram directly:
Instagram has "a separate actor per concept"; X/Twitter's
`apidojo/tweet-scraper` is "a single, search-driven actor" — every method
below sends different `searchTerms` (or a handle) to the *same* actor.
Profiles, posts, hashtags, and keywords are all just different search
queries over one tweet stream; "comments" don't exist as a concept on
X/Twitter at the API level — they're modeled here as reply tweets found via a
`conversation_id:` search.

### Imports
Same overall shape as the Instagram scraper (`register_scraper`,
`BaseScraper`/`ScrapeResult`, `get_settings`, `get_logger`, `Author`,
`PlatformName`, `get_or_register`), plus normalizers from
`app.normalization.twitter` instead of `.instagram`.

### `_bare_tweet_id(post_url_or_id: str) -> str` (module-level helper)
```python
def _bare_tweet_id(post_url_or_id: str) -> str:
    if "/status/" in post_url_or_id:
        return post_url_or_id.rsplit("/", 1)[-1].split("?", 1)[0]
    return post_url_or_id
```
Docstring: `conversation_id:` search needs the bare numeric tweet id, but
callers may pass a full `https://x.com/<user>/status/<id>` URL. Logic:
- If `"/status/"` appears anywhere in the string, take everything after the
  last `/` (`rsplit("/", 1)[-1]`) — the id segment — then strip any query
  string by splitting on `?` and keeping the part before it
  (`.split("?", 1)[0]`), handling URLs like
  `.../status/12345?s=20` correctly.
- Otherwise, assume the input is already a bare id and pass it through
  unchanged.
This is a private module function (leading underscore, no `self`) because
it's pure string parsing with no dependency on actor state — kept outside the
class for testability/clarity, not tied to an instance.

### `TwitterScraper` (`@register_scraper(PlatformName.TWITTER)`)

`platform = "twitter"`.

#### `scrape_profile(self, identifier: str) -> ScrapeResult`
Docstring explains a deliberate design choice: rather than rely on the
actor's `twitterHandles` profile-only mode ("less reliable across actor
versions"), this runs a `from:<handle>` search capped at `maxItems: 1` and
derives the `Author` from that one tweet's embedded `author` object via
`normalize_author`. `identifier.lstrip("@")` strips a leading `@` a caller
might pass. `authors = [normalize_author(items[0])] if items else []` —
defensive against an empty result (account with zero tweets, suspended, or
search returned nothing).

#### `scrape_posts(self, identifier: str, *, limit: int = 50) -> ScrapeResult`
Same `from:<handle>` search, `maxItems: limit`. Per-item loop identical in
shape to Instagram's `scrape_posts`: `get_or_register` for the author, then
`normalize_post`, wrapped in try/except logging "Skipping malformed tweet
item".

#### `scrape_comments(self, post_url_or_id: str, *, limit: int = 100) -> ScrapeResult`
Docstring: X/Twitter has no separate "comment" concept — replies are tweets
sharing the original's `conversation_id`. `tweet_id =
_bare_tweet_id(post_url_or_id)`, then searches
`f"conversation_id:{tweet_id}"`. Because the search can return the **original
tweet itself** alongside its replies, each item is checked: `raw_id =
str(item.get("id", "")); if raw_id in (tweet_id, post_url_or_id): continue` —
skips the original so it isn't miscounted as its own reply. Each surviving
item's comment gets `parent_id=item.get("inReplyToId")` — note this is
different from Instagram's approach (which sets `parent_id` explicitly to the
just-created top-level comment's id for nested replies): here, X's own
`inReplyToId` field on each tweet is trusted directly, since the flat
`conversation_id:` search doesn't naturally nest into a reply tree the way
Instagram's actor response does (Instagram embeds `replies`/`childComments`
inside each top-level item; X's search returns a flat list where each item
already knows its own parent via `inReplyToId`).

#### `scrape_hashtag(self, hashtag: str, *, limit: int = 50) -> ScrapeResult`
Searches `f"#{hashtag.lstrip('#')}"` (defensively strips then re-adds `#`, so
callers can pass `"climate"` or `"#climate"` interchangeably). Otherwise
structurally identical to `scrape_posts`.

#### `scrape_keyword(self, keyword: str, *, limit: int = 50) -> ScrapeResult`
Searches the raw `keyword` string as-is (`{"searchTerms": [keyword], ...}`) —
no hashtag-stripping since it's meant for free-text search terms, not tags.
Structurally identical to `scrape_posts`/`scrape_hashtag` otherwise. This is
the one platform where `scrape_keyword` is actually implemented rather than
falling back to `BaseScraper`'s `NotImplementedError` default — Instagram has
no `scrape_keyword` override at all (its actor family has no free-text search
actor), so calling `InstagramScraper().scrape_keyword(...)` raises
`NotImplementedError("instagram does not support keyword search")`.

**Where `TwitterScraper` is actually used (grepped):** identical pattern to
Instagram —
- Registered via `app/apify/twitter/__init__.py:1` → `app/apify/__init__.py:47`.
- Reached in production only through `get_scraper(PlatformName.TWITTER)` /
  `get_scraper("twitter")` inside `app/services/scrape_service.py`'s five
  `scrape_*` methods.
- Directly constructed in `tests/unit/test_apify_scrapers.py:213` (default
  runner) and `:222,250,269,282` (fake runner, one per tested method:
  profile/posts/hashtag/keyword — `scrape_comments` at line 250 per the class
  block `TestTwitterScraper` starting at line 208).
- CLI: `python scripts/run_scrape.py twitter hashtag climate --limit 100`
  (example in `scripts/run_scrape.py:9`).

---

# apify/youtube

## `app/apify/youtube/__init__.py`

```python
from app.apify.youtube.scraper import YouTubeScraper

__all__ = ["YouTubeScraper"]
```
Same re-export + registration-trigger role, imported by
`app/apify/__init__.py:47`.

## `app/apify/youtube/scraper.py`

Module docstring calls out YouTube as structurally unique among the three
platforms: **one raw item produces two distinct domain rows for the same
real-world "video"** — a `Post` (so it slots into the same unified
content/retrieval model every other platform's content uses) and a `Video`
(so duration/transcript-specific fields have a home that doesn't pollute the
generic `Post` schema). Likewise, a video's embedded channel info produces
both an `Author` (generic profile, matching every other platform) and a
`Channel` (subscriber-count-specific semantics). It also documents which
actor backs which method: `streamers/youtube-scraper` for
profile/posts (different `startUrls` target: channel landing page vs. `/videos`
tab), `streamers/youtube-comments-scraper` for comments, and
`pintostudio/youtube-transcript-scraper` for transcripts (explicitly
"best-effort, not guaranteed available"). Finally: YouTube has no
hashtag/keyword search via these actors, so `scrape_hashtag`/`scrape_keyword`
are "intentionally left unimplemented" — relying on `BaseScraper`'s default
`NotImplementedError`, which the docstring states outright "is correct here
and is not overridden."

### Imports
Adds `Channel` (alongside `Author`) since this scraper is the only one that
builds `Channel` objects, and imports six normalizer functions from
`app.normalization.youtube` (`normalize_author`, `normalize_channel`,
`normalize_comment`, `normalize_post`, `normalize_transcript_items`,
`normalize_video`) — every other platform imports at most three.

### Module constant

```python
_TRANSCRIPT_FETCH_LIMIT = 10
```
Comment explains the reasoning directly: fetching a transcript is a
**separate Apify actor run per video**. Above this many videos in one
`scrape_posts` call, skipping transcript fetches "keeps a 'scrape recent
posts' call fast" — doing it for e.g. a 50-video batch would multiply run
count (and wall-clock time) by 50, for a field that's a nice-to-have, not a
hard requirement of any downstream consumer.

### `YouTubeScraper` (`@register_scraper(PlatformName.YOUTUBE)`)

`platform = "youtube"`.

#### `scrape_profile(self, identifier: str) -> ScrapeResult`
Calls `apify_youtube_scraper_actor` with `startUrls=[{"url":
f"https://www.youtube.com/{identifier}"}]` (the channel's landing page) and
`maxResults: 1`. Docstring: pointing at the landing page and capping results
at 1 is enough to get one video item with the channel's info embedded, which
is all `normalize_author`/`normalize_channel` need — it doesn't need an
actual "profile-only" actor mode. `if not items: return
ScrapeResult(raw_item_count=0)` — explicit early-exit guard for a channel
with no public videos (where the profile-via-video-item trick can't work).
Otherwise takes `items[0]`, builds `author = normalize_author(raw)`, then
`channel = normalize_channel(raw, author_id=str(author.id))` (the `Channel`
needs a foreign key to its `Author` row), returning both plus
`raw_item_count=len(items)`.

#### `scrape_posts(self, identifier: str, *, limit: int = 50) -> ScrapeResult`
Same actor, but `startUrls` points at the channel's `/videos` tab — the
docstring explains this specific difference is what makes the actor list
*multiple* videos rather than just the channel's featured/landing content.
`maxResults: limit`.

Per raw item, the loop is denser than other platforms' because of the
dual-model-per-item structure:
1. `author = get_or_register(authors_by_key, normalize_author(raw), lambda a:
   a.dedup_key)` — deduped, since every video from the same channel repeats
   the same embedded channel info.
2. `channel = get_or_register(channels_by_key, normalize_channel(raw,
   author_id=str(author.id)), lambda c: c.dedup_key)` — likewise deduped, keyed
   by `Channel.dedup_key` (`f"{platform}:{platform_channel_id}"`, per
   `app/models/pydantic/channel.py:34-35`).
3. `post = normalize_post(raw, author_id=str(author.id))`.
4. `video = normalize_video(raw, channel_id=str(channel.id),
   post_id=str(post.id))` — links the `Video` row to both its `Channel` and
   its sibling `Post` (both by their client-generated ids at this point;
   `IngestionPipeline._run` remaps both after persistence, lines 172-184).

All four steps for one item live inside one `try/except Exception:
logger.warning("Skipping malformed YouTube video item", exc_info=True);
continue` — if *any* of the four normalize calls fails, the whole item
(author/channel/post/video together) is skipped, since a `Video` without a
valid `Post`/`Channel` id would be structurally broken.

Then, still per item, the transcript step:
```python
if limit <= _TRANSCRIPT_FETCH_LIMIT and video.video_url:
    try:
        transcript_items = await self.runner.run_and_fetch(
            settings.apify_youtube_transcript_actor, {"videoUrl": video.video_url}
        )
        text = normalize_transcript_items(transcript_items)
        if text:
            video = video.model_copy(update={"transcript": text})
    except Exception:
        logger.warning("Transcript fetch failed, continuing without it", ...)
```
Gated by the `_TRANSCRIPT_FETCH_LIMIT` constant explained above, and further
guarded by `video.video_url` being truthy (no URL to fetch a transcript for).
`video.model_copy(update={"transcript": text})` — since `Video` is an
immutable-by-convention Pydantic model built via `normalize_video` without a
transcript, this is how the transcript gets attached afterward without
mutating fields directly. Any failure here (rate limit, no transcript
available, actor error) is caught and logged but never propagates — per the
module docstring, a transcript is "best-effort, not guaranteed available."

Returns a `ScrapeResult` with all four collections populated:
`posts`, `authors` (deduped), `channels` (deduped), `videos` (one per raw
item, in item order — not deduped, since each video is unique even if its
channel repeats), plus `raw_item_count`.

#### `scrape_comments(self, post_url_or_id: str, *, limit: int = 100) -> ScrapeResult`
Calls `apify_youtube_comment_actor` (`streamers/youtube-comments-scraper`)
with `startUrls=[{"url": url}]`, `maxComments: limit`, where `url` is
either the caller's input as-is (if it already `startswith("http")`) or built
as `f"https://www.youtube.com/watch?v={post_url_or_id}"` for a bare video id.
Otherwise structurally identical to Instagram's/Twitter's comment scraping:
per-item `get_or_register` for the author, `normalize_comment(item,
post_id=post_url_or_id, author_id=...)`, with `post_url_or_id` passed through
as a placeholder `post_id` remapped later by the ingestion pipeline — same
pattern and same reasoning as Instagram's `scrape_comments` docstring
explains.

**Where `YouTubeScraper` is actually used (grepped):**
- Registered via `app/apify/youtube/__init__.py:1` → `app/apify/__init__.py:47`.
- Reached in production only via `get_scraper(PlatformName.YOUTUBE)` /
  `get_scraper("youtube")` in `app/services/scrape_service.py`.
- Directly constructed in `tests/unit/test_apify_scrapers.py:338` (default
  runner) and `:344,368,400,418` (fake runner; `TestYouTubeScraper` class
  starting at line 333, covering profile/posts/comments and the
  transcript-fetch branch).
- CLI: `python scripts/run_scrape.py youtube comments dQw4w9WgXcQ --limit 200`
  (example in `scripts/run_scrape.py:10`).

---

# normalization

## `app/normalization/__init__.py`

### Imports
- `from app.models.pydantic.enums import PlatformName` — enum used as
  `NORMALIZERS`'s dict keys.
- `from app.normalization import instagram, twitter, youtube` — imports the
  three per-platform normalizer *modules themselves* (not individual
  functions) so they can be stored as dict values and later called generically
  as `NORMALIZERS[platform].extract_engagement(post)`.
- `from app.normalization.common import as_int, dedupe_by_key, first_present,
  get_or_register, merge_prefer_non_null` — re-exports the shared helpers
  (see `common.py` below) so callers can `from app.normalization import
  dedupe_by_key` instead of reaching into `.common`.

### `NORMALIZERS` (module-level dict constant)

```python
NORMALIZERS = {
    PlatformName.INSTAGRAM: instagram,
    PlatformName.TWITTER: twitter,
    PlatformName.YOUTUBE: youtube,
}
```
The docstring comment directly above it states its purpose: "Registry used by
the ingestion pipeline to reach a platform's `extract_engagement` (and other
normalizer functions) generically, so `app/ingestion/pipeline.py` never
branches on platform by name." This is the **normalization-layer counterpart**
to `app/apify/__init__.py`'s `_REGISTRY` for scrapers — same "avoid an
if/elif per platform" design goal, but implemented as a plain module-level
dict of *modules* rather than a decorator-populated dict of *classes*, because
normalizer functions are free functions grouped by module, not methods on a
class with a common base.

Each value is literally the imported Python module object (`instagram`,
`twitter`, `youtube`), so `NORMALIZERS[PlatformName.INSTAGRAM].normalize_post`
would also work — but in practice the only attribute actually accessed
through this dict anywhere in the app is `.extract_engagement` (see below).

**Where used (grepped):** exactly one call site outside this package —
`app/ingestion/pipeline.py:309`, inside `IngestionPipeline._ingest_engagement`:
```python
normalizer = NORMALIZERS[post.platform]
engagement = normalizer.extract_engagement(post).model_copy(
    update={"post_id": persisted_post_id}
)
await self.engagement_repo.upsert_for_post(engagement)
```
`post.platform` is a `PlatformName` (well — because `BaseSchema` sets
`use_enum_values=True`, per the pattern noted in
`docs/embedding_model_explained.md`, it's actually stored as the plain string
value at runtime; `NORMALIZERS`'s keys are `PlatformName` enum members, whose
`str`/hash equality with their own `.value` is exactly what `StrEnum` — the
Instagram/Youtube/Twitter/etc. enum base — guarantees, which is why the dict
lookup by the stored string still works). For each persisted post, this looks
up the right platform module and calls its `extract_engagement(post)` — see
below — to build an `Engagement` row, remaps `post_id` to the now-persisted
post's real id, and upserts it. Wrapped in the same per-post
`try/except Exception: report.errors.append(...)` isolation pattern used
throughout `IngestionPipeline`.

Note `NORMALIZERS` currently only covers three platforms (Instagram, Twitter,
YouTube) — matching exactly the three scrapers registered in
`app/apify/__init__.py`. `PlatformName` itself defines more values
(`reddit`, `linkedin`, `facebook`, `tiktok`, `news`, per
`docs/embedding_model_explained.md`'s enum table) that have neither a
registered scraper nor a `NORMALIZERS` entry yet — attempting
`NORMALIZERS[PlatformName.REDDIT]` would raise a plain `KeyError`, and
`get_scraper(PlatformName.REDDIT)` would raise the app's own
`UnsupportedPlatformError` — consistent with `PlatformName` being defined
ahead of full platform support, as forward-looking schema.

### `extract_engagement` — what it actually does

`extract_engagement` is not defined in `__init__.py` itself; it's defined
**once per platform module** (`instagram.py`, `twitter.py`, `youtube.py` —
see each below) with an identical signature: `extract_engagement(post: Post)
-> Engagement`. The pattern in every implementation is the same: read
platform-specific counters back out of `post.platform_metadata` (a free-form
dict each `normalize_post` stashed counters into at normalization time — see
each normalizer below) and repackage them into the platform-agnostic
`Engagement` model's fields (`likes`, `views`, `shares`, `comments_count`).
This is the mechanism that lets `IngestionPipeline._ingest_engagement` build
an `Engagement` row for *any* platform through one line of generic code
(`NORMALIZERS[post.platform].extract_engagement(post)`) instead of a
per-platform if/elif — the "platform differences live inside
`app.normalization`, not inside `app.ingestion`" design goal stated in the
`app/apify/__init__.py` docstring extends to this ingestion-side counterpart
too.

---

## `app/normalization/common.py`

Module docstring: shared normalization helpers for deduplication and
merge-on-conflict logic, "used by every platform normalizer and by the
ingestion pipeline," kept platform-agnostic specifically so
`app/ingestion/pipeline.py` can dedupe a mixed batch without knowing which
platform produced any given item.

### Imports
- `from collections.abc import Callable, Iterable` — used in generic type
  hints (`Callable[[T], str]`, `Iterable[T]`).
- `from typing import Any` — used in `merge_prefer_non_null`/`first_present`'s
  dict value types, since Apify raw JSON values are untyped.

### `dedupe_by_key[T](items: Iterable[T], key_fn: Callable[[T], str]) -> list[T]`
```python
def dedupe_by_key[T](items, key_fn):
    seen: dict[str, T] = {}
    for item in items:
        seen[key_fn(item)] = item
    return list(seen.values())
```
Uses PEP 695 generic syntax (`def dedupe_by_key[T](...)`, no `TypeVar` import
needed — Python 3.12+). Collapses `items` to one-per-key, **keeping the last
occurrence** of each key (each iteration's `seen[key_fn(item)] = item`
overwrites any prior entry for that key). Docstring explains why "last wins"
specifically: ingestion processes items in scrape order, and a later page of
results (e.g. an updated comment count on the same post) should win over an
earlier one within the same batch.

**Where used (grepped):** `app/ingestion/pipeline.py:143,150,161,172,187` —
called once per entity type at the top of `IngestionPipeline._run`, on
`result.authors`, `result.channels`, `result.posts`, `result.videos`,
`result.comments` respectively, each keyed by that model's `.dedup_key`
property (`f"{platform}:{platform_*_id}"`, defined per-model in
`app/models/pydantic/{author,channel,post,comment}.py`). Also directly unit
tested in `tests/unit/test_normalization.py:15-31` (last-wins-on-duplicate,
preserves-first-occurrence-insertion-order, empty-input).

### `get_or_register[T](cache: dict[str, T], item: T, key_fn: Callable[[T], str]) -> T`
```python
def get_or_register[T](cache, item, key_fn):
    key = key_fn(item)
    if key not in cache:
        cache[key] = item
    return cache[key]
```
This one has the longest docstring in the file because the bug it prevents is
subtle. Every scraper normalizes **one fresh `Author` per raw item** (a
post/comment/video each carries its own embedded owner info) — so the "same"
real-world author gets a brand-new object with a brand-new client-generated
`.id` (via `uuid.uuid4()`, from `IdentifiedMixin`) on every occurrence across
a batch. If a scraper instead called `normalize_author(item)` fresh for every
post and stamped `post.author_id` from *that* object's id, then deduped the
full author list afterward with `dedupe_by_key` (last-wins), every post that
referenced an *earlier* occurrence's id would end up with a dangling/wrong FK
— a real foreign-key violation once persisted, since only the last-seen
author id survives the dedup pass.

`get_or_register` avoids this by building the cache **while iterating**: on
first sight of a given key, it caches that exact object as canonical; on every
subsequent sight, it returns the **already-cached (first-seen)** object
instead of the newly-normalized one — so every post/comment/video that
embeds the same real-world author reads `.id` off the *same* object from the
start. No separate dedupe pass over authors is needed afterward — the
`authors_by_key.values()` returned by each scraper is already deduped by
construction.

Note the asymmetry with `dedupe_by_key`: `dedupe_by_key` is last-wins (used
for the *outer* Post/Comment/etc. lists in `IngestionPipeline._run`, where
later data should win), while `get_or_register` is effectively first-wins for
the object identity kept as canonical (though it doesn't examine field-level
freshness at all — see `merge_prefer_non_null` below for that finer-grained
concern, which the current code doesn't actually invoke anywhere, per the
grep below).

**Where used (grepped):** every platform scraper's `scrape_posts`,
`scrape_comments`, `scrape_hashtag`, `scrape_keyword` loops call it once per
raw item to dedupe the item's embedded author against a local
`authors_by_key` cache:
`app/apify/instagram/scraper.py:63,95,106,140`,
`app/apify/twitter/scraper.py:75,111,141,165`,
`app/apify/youtube/scraper.py:107,110 (author *and* channel),178`.
Imported into `app/normalization/__init__.py`'s `__all__` and re-exported;
each scraper imports it as `from app.normalization import get_or_register`
(not `from app.normalization.common import ...`) — confirmed by grep across
all three scraper files.

### `merge_prefer_non_null(existing: dict, incoming: dict) -> dict[str, Any]`
```python
def merge_prefer_non_null(existing, incoming):
    merged = dict(existing)
    for key, value in incoming.items():
        if value is None or value == "" or value == [] or value == {}:
            continue
        merged[key] = value
    return merged
```
Field-by-field merge: `incoming` overwrites `existing` **unless** the
incoming value is "empty" (`None`, `""`, `[]`, or `{}`), in which case the
existing value is kept. Docstring's motivating example: reconciling a
duplicate author scraped in two different runs — bio populated in one run,
missing in the other — without ever regressing a previously-known field back
to null just because a later scrape happened not to capture it.

**Where used (grepped):** it is **exported** (`app/normalization/__init__.py`'s
`__all__`) and **directly unit tested**
(`tests/unit/test_normalization.py:34-58`, four tests: incoming-overrides,
keeps-existing-on-none, keeps-existing-on-empty-string/list/dict, adds-new-keys)
— but grepping the rest of `app/` (scrapers, `app/ingestion/pipeline.py`,
`app/repositories/*`) turns up **no call site outside its own test file**. Like
`ScrapeResult.merge`, this is a ready-but-unused combinator: the current
ingestion path resolves author duplicates via `get_or_register`'s
first-seen-wins *within a single scrape batch*, and via each repository's
upsert-on-conflict at the database layer *across* runs — neither path
currently calls back into `merge_prefer_non_null` to do a field-level
non-null-preferring merge before upserting. It exists, and is tested, as
available (and presumably intended-for-future-use) infrastructure.

### `first_present(source: dict, *keys: str, default: Any = None) -> Any`
```python
def first_present(source, *keys, default=None):
    for key in keys:
        if key in source and source[key] is not None:
            return source[key]
    return default
```
Docstring: Apify actors are "not internally consistent about field naming
across actor versions (e.g. `commentsCount` vs `comments_count` vs
`commentCount`)"; every normalizer uses this instead of a long if/elif chain.
Note the check is `key in source and source[key] is not None` — a key
present with an explicit `None` value is treated the same as a missing key
(falls through to the next candidate key), but a key present with a falsy-but-
real value (`0`, `False`, `""`) is returned immediately, since `0 is not None`
is `True`. This matters, e.g., for a genuinely-zero `likesCount`.

**Where used (grepped):** pervasively — every field read in
`normalize_author`/`normalize_post`/`normalize_comment`/`normalize_channel`/
`normalize_video` across `app/normalization/instagram.py`,
`.../twitter.py`, and `.../youtube.py` goes through `first_present` for any
field whose key name might have drifted across actor versions. Also directly
unit tested in `tests/unit/test_normalization.py:62-82` (first-key-match,
falls-back-to-next-key, skips-None-values, default-when-missing,
default-is-None-by-default).

### `as_int(value: Any) -> int | None`
```python
def as_int(value):
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
```
Defensive numeric coercion: Apify counters sometimes arrive as strings
(`"42"`), floats (`3.9` → truncates to `3` via `int()`), or occasionally
malformed/missing values. Returns `None` (rather than raising or defaulting to
`0`) on anything uncoercible, so a genuinely-unknown count is stored as
`None`/`NULL` rather than a misleading `0`. Directly unit tested in
`tests/unit/test_normalization.py:86-98` (numeric-string, float-truncation,
None passthrough, non-numeric-string → None).

**Where used (grepped):** every `*Count`/`*count` field across all three
platform normalizers' `normalize_author`/`normalize_post`/`normalize_comment`
functions (e.g. `follower_count`, `likes_count`, `comments_count`,
`view_count`, etc.) — dozens of call sites in
`instagram.py`/`twitter.py`/`youtube.py`.

### `as_float(value: Any) -> float | None`
Same shape/pattern as `as_int` but coerces to `float` and catches
`(TypeError, ValueError)`. Grepping for its call sites: it is **not called
anywhere** in `instagram.py`, `twitter.py`, or `youtube.py` (all three use
`_parse_duration` for their one float-valued field, `duration_seconds`, which
does its own bespoke "seconds or HH:MM:SS string" parsing instead — see
`youtube.py` below) — and it is not re-exported in
`app/normalization/__init__.py`'s `__all__` (only `as_int` is). It appears to
be written as a natural counterpart to `as_int` for a numeric-float use case
that hasn't materialized in any current normalizer, and has no direct test
in `tests/unit/test_normalization.py` either (only `as_int` is tested there).

---

## `app/normalization/instagram.py`

Module docstring: maps raw Apify Instagram actor output into unified Pydantic
models; field names are read defensively via `first_present` "because
Apify's Instagram actors (profile/post/hashtag/comment scrapers) have changed
field naming across versions... and this project should keep working across
actor upgrades without a code change."

### Imports
- `from datetime import datetime` — return type of `_parse_timestamp`.
- `from app.models.pydantic import Author, Comment, Engagement, Media, Post`
  — every model this module constructs.
- `from app.models.pydantic.enums import ContentType, MediaType, PlatformName`
  — `ContentType`/`MediaType` classify posts/media; `PlatformName.INSTAGRAM`
  is stamped on every model built here.
- `from app.normalization.common import as_int, first_present` — the shared
  helpers used throughout this module (see above).
- `from app.utils.text import extract_hashtags, extract_mentions,
  extract_urls` — regex-based text-extraction fallbacks used when Instagram's
  raw item doesn't include a structured `hashtags`/`mentions` field, applied
  to the post caption / comment text instead.

### `_CONTENT_TYPE_MAP` (module constant)
```python
_CONTENT_TYPE_MAP = {
    "video": ContentType.REEL,
    "clips": ContentType.REEL,
    "igtv": ContentType.VIDEO,
    "sidecar": ContentType.POST,
    "image": ContentType.POST,
    "feed": ContentType.POST,
}
```
Maps Instagram's raw `productType`/`type` string values (lowercased before
lookup) onto the app's own `ContentType` enum. `"video"`/`"clips"` → `REEL`
(Instagram's Reels product surfaces use both terms across actor
versions/eras); `"igtv"` → `VIDEO` (longer-form, distinct from a Reel);
`"sidecar"` (Instagram's internal name for a multi-image/video carousel post),
`"image"`, and `"feed"` all → the generic `POST`. Any raw type not in this map
falls back to `ContentType.POST` via `_CONTENT_TYPE_MAP.get(raw_type,
ContentType.POST)` in `normalize_post`.

### `_parse_timestamp(value: str | int | float | None) -> datetime | None`
```python
def _parse_timestamp(value):
    if value is None:
        return None
    if isinstance(value, int | float):
        return datetime.fromtimestamp(value, tz=None)
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
```
Handles two shapes Instagram actors return timestamps in: a Unix epoch
number (`int`/`float`, via `takenAt` on some actor versions) → converted with
`datetime.fromtimestamp`, or an ISO-8601 string (possibly with a trailing
`Z` for UTC, which Python's `fromisoformat` didn't accept pre-3.11 — replaced
with `+00:00` for compatibility) → parsed with `datetime.fromisoformat`. Any
unparseable string returns `None` rather than raising, consistent with this
whole module's "degrade gracefully" philosophy. Note: `isinstance(value, int
| float)` is PEP 604 union syntax used directly inside `isinstance` (valid in
Python 3.10+).

### `normalize_author(raw: dict) -> Author`
Builds an `Author` from an Instagram profile-or-post-owner payload:
- `platform=PlatformName.INSTAGRAM`.
- `platform_user_id` — `first_present(raw, "ownerId", "id", "userId", "pk",
  default=raw.get("username", ""))` — tries four possible id keys in order,
  falling back to the username string itself if truly none are present
  (better than an empty id for dedup-key purposes).
- `username` — `first_present(raw, "ownerUsername", "username",
  default="unknown")`.
- `display_name`, `bio` — `first_present(raw, "ownerFullName", "fullName",
  "full_name")` / `first_present(raw, "biography", "bio")`.
- `profile_url` — prefers an explicit `url`/`profileUrl` field, else
  constructs `https://www.instagram.com/{username}/` from whatever username
  was resolved above.
- `avatar_url` — `first_present(raw, "profilePicUrl", "profilePicUrlHD")`.
- `is_verified`, `is_private` — coerced with `bool(first_present(...,
  default=False))`.
- `follower_count`, `following_count`, `post_count` — each `as_int(...)` over
  a `first_present(...)` pair of possible key names.
- `external_url` — `raw.get("externalUrl")` directly (no drift concern noted
  for this one field).
- `platform_metadata` — a dict comprehension keeping every raw key **except**
  the ones already promoted to first-class fields above (`ownerUsername`,
  `username`, `ownerFullName`, `fullName`, `biography`, `bio`,
  `profilePicUrl`) — preserves the full raw payload for anything not
  explicitly modeled, without duplicating data already captured in typed
  fields. (Note this exclusion list is not fully exhaustive of every promoted
  field — e.g. `ownerId`/`id`/`pk`, `verified`/`isVerified`, counts, etc. are
  *not* excluded and so also survive into `platform_metadata` alongside their
  typed counterparts; this looks like a partial/best-effort exclusion rather
  than a complete one.)

### `normalize_post(raw: dict, *, author_id: str) -> Post`
- `caption` — `first_present(raw, "caption", "text", default="") or ""`
  (double-guards against `first_present` returning `None`).
- `content_type` — lowercases `first_present(raw, "productType", "type",
  default="feed")` and looks it up in `_CONTENT_TYPE_MAP`.
- `media` — builds a `list[Media]` from up to three raw shapes: a top-level
  `displayUrl` (image), each entry in `childPosts` (a carousel's individual
  slides — video if that child has a `videoUrl`, else image), and a top-level
  `videoUrl` (e.g. for a Reel). Every `Media(post_id=None, ...)` is
  constructed with `post_id=None` because the `Post` itself doesn't have a
  persisted id yet at normalization time — `IngestionPipeline._ingest_media`
  fills in the real `post_id` after the post is upserted (see
  `app/ingestion/pipeline.py:230-248`, which explicitly re-copies each
  `Media` with `post_id=persisted_post_id`).
- `url` — explicit `url` field if present, else constructed from
  `shortCode`.
- `hashtags`/`mentions` — prefer the actor's own structured `hashtags`/
  `mentions` arrays if present, else fall back to regex extraction
  (`extract_hashtags(caption)`/`extract_mentions(caption)`) — `raw.get(...)
  or extract_...(caption)` pattern.
- `urls` — always regex-extracted from the caption (`extract_urls(caption)`;
  Instagram's raw payload has no structured URL list to prefer).
- `posted_at` — `_parse_timestamp(first_present(raw, "timestamp",
  "takenAt"))`.
- `is_sponsored` — `bool(first_present(raw, "isSponsored", default=False))`.
- `location` — `raw.get("locationName")`.
- `platform_metadata` — deliberately a **small, fixed** dict here (unlike
  `normalize_author`'s "everything except..." approach): `likes_count`,
  `comments_count`, `video_view_count`, `video_play_count`, each coerced with
  `as_int`. These four specific keys are exactly what `extract_engagement`
  (below) reads back out — this dict is effectively a private contract
  between `normalize_post` and `extract_engagement`, not a general-purpose
  raw-payload dump.

### `normalize_comment(raw, *, post_id, author_id, parent_id=None) -> Comment`
- `content` — `text or "(no text)"` — guarantees `Comment.content` (presumably
  a required/non-empty field) is never blank, using a visible placeholder
  instead.
- `likes`, `reply_count` — `as_int` over `first_present` pairs
  (`likesCount`/`likeCount`, `repliesCount`/`replyCount`).
- `hashtags`/`mentions` — always regex-extracted from the comment text (no
  structured-field alternative offered by comment actors).
- `posted_at` — `_parse_timestamp(first_present(raw, "timestamp",
  "createdAt"))`.
- `platform_metadata` — just `{"owner_username": ...}`.
- `post_id`/`author_id`/`parent_id` are passed straight through from the
  caller (the scraper), not derived from `raw` — these are the
  scraper-supplied linkage ids described in `instagram/scraper.py` above.

### `extract_engagement(post: Post) -> Engagement`
```python
def extract_engagement(post: Post) -> Engagement:
    meta = post.platform_metadata
    return Engagement(
        likes=meta.get("likes_count"),
        views=meta.get("video_view_count") or meta.get("video_play_count"),
        comments_count=meta.get("comments_count"),
    )
```
Docstring: builds an `Engagement` row from the counters `normalize_post`
stashed in `platform_metadata` — and notes explicitly "Instagram exposes no
'shares' signal," so `Engagement.shares` is left at its model default
(presumably `None`/`0`) rather than set here. `views` prefers
`video_view_count`, falling back to `video_play_count` if the former is
absent — two different Instagram actor-version field names for effectively
the same concept. This is the function called generically via
`NORMALIZERS[PlatformName.INSTAGRAM].extract_engagement(post)` in
`app/ingestion/pipeline.py:310` (see the `NORMALIZERS` section above), and
directly unit tested in
`tests/unit/test_normalization.py:263-272` (`test_instagram_extract_engagement_maps_metadata_keys`).

---

## `app/normalization/twitter.py`

Module docstring: maps raw `apidojo/tweet-scraper`-shaped output into unified
models; uses `first_present` "for the same reason as
`app.normalization.instagram`: actor field naming drifts across versions."

### Imports
Same shape as `instagram.py`'s, targeting the same five models
(`Author, Comment, Engagement, Media, Post`).

### `_parse_timestamp(value: str | None) -> datetime | None`
```python
def _parse_timestamp(value):
    if not value:
        return None
    for fmt in (None, "%a %b %d %H:%M:%S %z %Y"):
        try:
            if fmt is None:
                return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
            return datetime.strptime(value, fmt)
        except ValueError:
            continue
    return None
```
Twitter/X's raw API historically returns timestamps in its own idiosyncratic
format (`"Wed Oct 05 20:12:47 +0000 2021"`, i.e.
`"%a %b %d %H:%M:%S %z %Y"`), while some Apify actor versions normalize to
plain ISO-8601. This function tries ISO-8601 first (`fmt is None` branch),
then falls back to the classic Twitter format string, returning `None` if
neither parses — the same "try multiple formats, degrade to None" philosophy
as Instagram's timestamp parser, just with two different candidate *formats*
instead of two different candidate *field names*.

### `normalize_author(raw: dict) -> Author`
- `author_raw = raw.get("author", raw)` — a tweet item embeds its author
  under an `"author"` key, but this function is also used to normalize a
  standalone profile payload (no `"author"` wrapper) — falling back to `raw`
  itself when `"author"` is absent handles both shapes with one function.
- Fields read from `author_raw` (not `raw`) from here on: `id`/`userId`/
  `restId` → `platform_user_id`; `userName`/`username`/`screen_name` →
  `username`; `name`/`displayName` → `display_name`; `description`/`bio` →
  `bio`; `profile_url` constructed as `https://x.com/{username}`;
  `profilePicture`/`profileImageUrl` → `avatar_url`; `isVerified`/
  `isBlueVerified`/`verified` → `is_verified` (three candidate keys —
  reflecting X's own product history of "legacy verified" vs. "Blue
  verified"); `followers`/`followersCount` → `follower_count`;
  `following`/`followingCount` → `following_count`;
  `statusesCount`/`tweetsCount` → `post_count`; `location`, `url` (→
  `external_url`) read directly via `.get(...)` (no drift noted for these
  two).
- `platform_metadata` — keeps every `author_raw` key except `userName`,
  `username`, `name` (same "exclude the promoted keys" pattern as Instagram,
  similarly non-exhaustive of every field promoted above).

### `normalize_post(raw: dict, *, author_id: str) -> Post`
- `text` — `first_present(raw, "fullText", "text", default="")`.
- `entities = raw.get("entities") or {}` then extracts structured
  `hashtags`/`mentions`/`urls` from it, each handling two possible shapes per
  entry: a dict (`h.get("text", h)` / `m.get("username", m)` /
  `u.get("expanded_url", u)`) or a bare string/value (the ternary's `else`
  branch), via `h.get(...) if isinstance(h, dict) else h` list
  comprehensions — again defending against actor-version shape drift, this
  time at the *entity-item* level rather than the *top-level-key* level.
- `content_type` — defaults to `ContentType.TWEET`; upgraded to
  `ContentType.RETWEET` if `isRetweet` is truthy, else to `ContentType.QUOTE`
  if `isQuote` is truthy (checked in that order — a retweet flag wins over a
  quote flag if somehow both were set).
- `media_items` — iterates `raw.get("media", []) or
  raw.get("extendedEntities", {}).get("media", []) or []` (again, two
  possible raw locations for the media array across actor versions/tweet
  shapes), reading `media_url_https`/`mediaUrl`/`url` per item, and
  classifying `type in {"video", "animated_gif"}` as `MediaType.VIDEO`
  (grouping GIFs with video, since X serves animated GIFs as short
  auto-playing video files) else `MediaType.IMAGE`.
- `hashtags`/`mentions`/`urls` on the `Post` — prefer the structured
  `entities`-derived lists; fall back to regex extraction from `text` only if
  the structured list came back empty (`hashtags or extract_hashtags(text)`).
- `posted_at` — `_parse_timestamp(first_present(raw, "createdAt",
  "created_at"))`.
- `platform_metadata` — six counters: `retweet_count`, `reply_count`,
  `like_count` (from `likeCount`/`favorite_count` — `favorite_count` being
  the pre-2015 legacy Twitter API name for what's now "like"), `quote_count`,
  `view_count` (from `viewCount`/`views`), `bookmark_count` (only one
  candidate key, `raw.get("bookmarkCount")` directly, no `first_present`
  needed).

### `normalize_comment(raw, *, post_id, author_id, parent_id=None) -> Comment`
Structurally identical to Instagram's, mapping a reply tweet (found via the
scraper's `conversation_id:` search) to `Comment`: `content = text or "(no
text)"`, `likes`/`reply_count` via `as_int`+`first_present`, always
regex-extracted `hashtags`/`mentions`, `posted_at` parsed the same way as
`normalize_post`, and `platform_metadata={"in_reply_to_id":
raw.get("inReplyToId")}` — this is the field the scraper reads back
(`item.get("inReplyToId")`, in `TwitterScraper.scrape_comments`) to set each
reply's `parent_id` *before* calling this function — i.e. it's stashed into
`platform_metadata` here for observability/debugging even though the scraper
already extracted it independently for linkage purposes.

### `extract_engagement(post: Post) -> Engagement`
```python
def extract_engagement(post: Post) -> Engagement:
    meta = post.platform_metadata
    return Engagement(
        likes=meta.get("like_count"),
        views=meta.get("view_count"),
        shares=meta.get("retweet_count"),
        comments_count=meta.get("reply_count"),
    )
```
Docstring notes X's retweet count is what maps onto the unified `shares`
signal — this is the one of the three platforms whose `extract_engagement`
actually populates `Engagement.shares` (Instagram's and YouTube's both leave
it unset, per their own docstrings, since neither actor family exposes a
share/repost count). Directly unit tested at
`tests/unit/test_normalization.py:421-431`
(`test_twitter_extract_engagement_maps_metadata_keys`), and reached generically
via `NORMALIZERS[PlatformName.TWITTER].extract_engagement(post)` in
`app/ingestion/pipeline.py:310`.

---

## `app/normalization/youtube.py`

Module docstring: maps raw output from three different actors
(`streamers/youtube-scraper`, `streamers/youtube-comments-scraper`,
`pintostudio/youtube-transcript-scraper`) into unified models. States the
platform's defining quirk directly: "'author' and 'channel' are the same
real-world entity but two different tables... `normalize_author` and
`normalize_channel` are both derived from the same raw video/channel
payload."

### Imports
Adds `Channel` and `Video` (alongside `Author, Comment, Engagement, Media,
Post`) — the only normalization module that imports both, matching
`YouTubeScraper`'s unique dual-model-per-item structure.

### `_parse_timestamp(value: str | None) -> datetime | None`
Simpler than the other two platforms' — YouTube actor output is
consistently ISO-8601 (with a possible trailing `Z`), so this is just the
`fromisoformat` branch of Instagram's/Twitter's parsers, no fallback format.

### `_parse_duration(value: str | int | float | None) -> float | None`
```python
def _parse_duration(value):
    if value is None:
        return None
    if isinstance(value, int | float):
        return float(value)
    parts = str(value).split(":")
    if not all(p.isdigit() for p in parts):
        return None
    seconds = 0.0
    for part in parts:
        seconds = seconds * 60 + int(part)
    return seconds
```
Docstring: "Duration may arrive as seconds (number) or 'HH:MM:SS' / 'MM:SS'
text." If `value` is already numeric, just coerce to `float` directly. If
it's a string, split on `:` and validate every part is all-digits
(`p.isdigit()` — rejects e.g. a stray non-numeric part, returning `None` for
the whole value rather than a partially-wrong number); then converts a
variable-length `HH:MM:SS`/`MM:SS`/`SS` string to total seconds via the
classic "multiply running total by 60, add next part" accumulation — this
correctly handles 1, 2, or 3-part duration strings with the same loop (a
bare `"45"` → `0*60+45 = 45`; `"3:45"` → `(0*60+3)*60+45 = 225`; `"1:03:45"`
→ `((0*60+1)*60+3)*60+45 = 3825`).

### `normalize_author(raw: dict) -> Author`
Docstring: maps "a video item's embedded channel info to `Author` (the
channel owner, normalized like every other platform's profile)."
- `channel_id` computed once via `first_present(raw, "channelId",
  "channelUrl", default="")` and reused for both `platform_user_id` and (if
  `channelUrl` is absent) the constructed `profile_url` fallback.
- `username` — `first_present(raw, "channelName", "channelUsername",
  default="unknown")`.
- `display_name`/`bio`/`avatar_url` read directly via `.get(...)`
  (`channelName`, `channelDescription`, `channelAvatarUrl` — no drift
  concern noted for these).
- `is_verified` — `bool(first_present(raw, "channelIsVerified",
  default=False))`.
- `follower_count` — `as_int(first_present(raw, "numberOfSubscribers",
  "subscriberCount"))` — YouTube's "subscribers" concept maps onto the
  generic `Author.follower_count` field (no YouTube-specific
  "subscriber_count" field on `Author`; that lives on `Channel` instead, see
  below).
- `post_count` — `as_int(first_present(raw, "channelTotalVideos",
  "videoCount"))`.
- `platform_metadata` — just `{"is_monetized": raw.get("isMonetized")}`
  (much smaller than Instagram's/Twitter's "everything except..." dumps —
  YouTube's raw channel fields are mostly already promoted to first-class
  `Author`/`Channel` fields).

### `normalize_channel(raw: dict, *, author_id: str) -> Channel`
Docstring: "Map a video item's embedded channel info to `Channel`." Same raw
payload as `normalize_author` above, different target model:
`platform_channel_id` (same `channelId`/`channelUrl` pair), `author_id`
(passed through, linking back to the `Author` built above), `name`
(`channelName`, default `"unknown"`), `description` (`channelDescription`),
`subscriber_count`/`video_count`/`total_views` (via `first_present`+`as_int`
over `numberOfSubscribers`/`subscriberCount`,
`channelTotalVideos`/`videoCount`, `channelTotalViews`/`channelViewCount`),
`country` (`channelLocation`), `platform_metadata={"joined_date":
raw.get("channelJoinedDate")}`.

### `normalize_post(raw: dict, *, author_id: str) -> Post`
Docstring: "Map a YouTube video item to `Post` (content_type=VIDEO/SHORT)."
- `description` — `first_present(raw, "text", "description", default="")`.
- `duration = _parse_duration(raw.get("duration"))`.
- `content_type` — `ContentType.SHORT if (duration is not None and duration
  <= 60) else ContentType.VIDEO` — the ≤60-second heuristic YouTube itself
  uses to distinguish Shorts from regular videos; note a video with unknown
  duration (`duration is None`) defaults to `VIDEO`, not `SHORT` (the safer
  default given no evidence either way).
- `media` — a single `Media(post_id=None, media_type=MediaType.VIDEO,
  url=video_url, thumbnail_url=thumbnail)` entry if a video URL is present
  (`first_present(raw, "url", "videoUrl")`) — unlike Instagram's/Twitter's
  possible multiple media items, a YouTube video item always maps to exactly
  one `Media` row. `thumbnail_url` is the one place across all three
  normalization modules where `Media` gets a thumbnail set explicitly at
  construction (Instagram/Twitter never pass `thumbnail_url` to `Media`).
- `caption` — `str(raw.get("title", ""))` (the video's **title**, not its
  description — a deliberate choice: on other platforms `caption` holds the
  primary user-facing text, and a YouTube video's "primary text" for feed
  display purposes is its title, not its long-form description).
- `content` — the full `description` text (distinct from `caption`/title —
  this is the field `IngestionPipeline._generate_embeddings` actually reads
  for `EmbeddingSourceType.POST` embeddings via `post.caption or
  post.content`, so for YouTube, if `title` is ever blank, embeddings fall
  back to the description).
- `hashtags` — `raw.get("hashtags") or extract_hashtags(description)`.
- `mentions`/`urls` — always regex-extracted from `description` (no
  structured alternative from this actor).
- `posted_at` — `_parse_timestamp(first_present(raw, "date", "uploadDate"))`.
- `location` — `raw.get("location")`.
- `platform_metadata` — `view_count`, `like_count` (from `raw.get("likes")`
  directly — no alternate key), `comments_count` (from
  `raw.get("commentsCount")` directly), `duration_seconds` (the already-parsed
  `duration` float, stashed here too so `extract_engagement`/other consumers
  don't need to re-parse it — though note `extract_engagement` below doesn't
  actually read `duration_seconds`).

### `normalize_video(raw: dict, *, channel_id: str, post_id: str | None = None) -> Video`
Docstring: "Map a YouTube video item to `Video` (duration/transcript
semantics)." Distinct from `normalize_post` in that it captures the fields
`Post` deliberately doesn't carry: `transcript` (`raw.get("transcript")` —
almost always absent at this call site, since the transcript actor runs
*after* this function via a separate call in `YouTubeScraper.scrape_posts`,
and is attached afterward via `video.model_copy(update={"transcript":
text})`), `duration_seconds` (`_parse_duration(raw.get("duration"))`,
re-parsed independently of `normalize_post`'s copy rather than shared),
`thumbnail_url`, `video_url` (`first_present(raw, "url", "videoUrl")`),
`published_at` (same `date`/`uploadDate` pair as `normalize_post`'s
`posted_at`), and its own small `platform_metadata={"view_count": ...}`.
`title`/`description` are duplicated here too (same source fields as
`normalize_post`'s `caption`/`content`) — since `Video` is meant to stand
independently queryable (e.g. by `VideoRepository`) without a join back to
`Post` for basic display fields.

### `normalize_transcript_items(items: list[dict]) -> str`
```python
def normalize_transcript_items(items):
    lines = [str(item.get("text", "")).strip() for item in items if item.get("text")]
    return " ".join(lines).strip()
```
Docstring: "Join transcript-actor line items (each with a `text` field) into
a single plain-text transcript string." The transcript actor
(`pintostudio/youtube-transcript-scraper`) returns one dataset item per
subtitle line/segment; this flattens them into one string, skipping any item
with no/empty `text`, joined with single spaces. The final `.strip()` removes
any leading/trailing whitespace left over from an empty-string edge case.
Called exactly once, at `app/apify/youtube/scraper.py:133`, immediately after
the transcript actor run inside `scrape_posts`'s per-video loop; its result
is only kept (`video.model_copy(update={"transcript": text})`) `if text` is
truthy — an all-empty transcript response leaves `Video.transcript` unset
rather than storing an empty string.

### `normalize_comment(raw, *, post_id, author_id, parent_id=None) -> Comment`
- `text` — `first_present(raw, "text", "comment", default="")` (two possible
  field names for the comment body across actor versions).
- `content = text or "(no text)"` — same placeholder pattern as the other two
  platforms.
- `parent_comment_id` — `parent_id or (raw.get("parentCommentId") if
  raw.get("isReply") else None)` — prefers an explicitly-passed `parent_id`
  (from the caller/scraper), but if none was passed, falls back to reading
  the raw item's own `parentCommentId` field *only if* `raw.get("isReply")`
  is truthy — i.e. a top-level comment with a stray `parentCommentId` field
  (if that ever occurred) would not be misclassified as a reply, since
  `isReply` must also be true.
- `likes` — `as_int(first_present(raw, "voteCount", "likesCount"))`
  (YouTube's own API historically calls this "vote count").
- `reply_count` — `as_int(raw.get("replyCount"))` (single key, no
  `first_present` needed).
- `hashtags`/`mentions` — regex-extracted from `text`.
- `posted_at` — `_parse_timestamp(raw.get("publishedAt"))` (single key).
- `platform_metadata` — `{"author_display_name": raw.get("author")}`.

### `__all__` — placed mid-file, before `extract_engagement`
```python
__all__ = [
    "normalize_author", "normalize_channel", "normalize_post", "normalize_video",
    "normalize_comment", "normalize_transcript_items", "extract_engagement",
]
```
Unusually, this module defines `__all__` **before** `extract_engagement`'s own
`def` (which appears directly below it in the file) rather than at the very
end — Python doesn't require `__all__` to be defined after everything it
lists (it's just a module attribute, evaluated at whatever point it's
reached, and only actually consulted later by `from module import *` or
introspection tools), so this works correctly, but it's a minor stylistic
inconsistency versus the other two normalization modules, neither of which
defines an explicit `__all__` at all.

### `extract_engagement(post: Post) -> Engagement`
```python
def extract_engagement(post: Post) -> Engagement:
    meta = post.platform_metadata
    return Engagement(
        likes=meta.get("like_count"),
        views=meta.get("view_count"),
        comments_count=meta.get("comments_count"),
    )
```
Docstring: builds an `Engagement` row from `normalize_post`'s stashed
counters; explicitly notes "YouTube exposes no 'shares' signal via these
actors" (so, like Instagram, `shares` is left unset). Directly unit tested at
`tests/unit/test_normalization.py:616-626`
(`test_youtube_extract_engagement_maps_metadata_keys`), and reached generically
via `NORMALIZERS[PlatformName.YOUTUBE].extract_engagement(post)` in
`app/ingestion/pipeline.py:310`.

---

## Summary — the full raw-item-to-persisted-row path

1. **`app/services/scrape_service.py`** (`ScrapeService.scrape_posts`, etc.,
   or `scripts/run_scrape.py`'s CLI) calls `get_scraper(platform)`
   (`app/apify/__init__.py`), which looks up the right `BaseScraper` subclass
   in `_REGISTRY` (populated at import time by each platform package's
   `@register_scraper` decorator) and instantiates it fresh.
2. The scraper's method (`scrape_profile`/`scrape_posts`/`scrape_comments`/
   `scrape_hashtag`/`scrape_keyword`) builds a platform-specific `run_input`
   dict and calls `self.runner.run_and_fetch(actor_id, run_input)`
   (`app/apify/base/client.py`'s `ApifyActorRunner`), which starts the Apify
   actor, blocks (off-thread) until it finishes, checks its terminal status,
   and returns every raw dataset item as a plain `list[dict]`.
3. For each raw dict, the scraper calls the matching platform's
   `normalize_*` functions (`app/normalization/{instagram,twitter,youtube}.py`)
   to build validated Pydantic models (`Author`, `Post`, `Comment`, and for
   YouTube also `Channel`/`Video`), using `get_or_register`
   (`app/normalization/common.py`) to dedupe repeated embedded-author info
   within the batch while keeping every cross-reference (`author_id`, etc.)
   consistent. Malformed items are individually caught, logged, and skipped
   (never abort the whole scrape).
4. All the normalized models for one scrape call are packaged into one
   `ScrapeResult` (`app/apify/base/scraper.py`) and handed to
   `IngestionPipeline.ingest` (`app/ingestion/pipeline.py`), which further
   deduplicates each list via `dedupe_by_key` (last-wins across the whole
   batch), persists everything through the repository layer with FK
   remapping from client-generated ids to DB-persisted ids, and — for every
   persisted post — looks up `NORMALIZERS[post.platform]`
   (`app/normalization/__init__.py`) and calls its `extract_engagement(post)`
   to build and upsert an `Engagement` row from the counters each
   `normalize_post` had stashed in `Post.platform_metadata`.
5. Finally, `IngestionPipeline._generate_embeddings` turns persisted
   posts/comments/video-transcripts into embeddings — the point where this
   flow hands off to the process documented in
   `docs/embedding_model_explained.md`.

Two small pieces of infrastructure are defined but not currently wired into
any live call path: `ScrapeResult.merge` (`app/apify/base/scraper.py`) and
`merge_prefer_non_null` (`app/normalization/common.py`) — both are tested
(the latter directly, the former only indirectly via `ScrapeResult`
construction in pipeline tests) but have no production call site as of this
writing; they read as forward-looking API for a "merge multiple scrapes /
reconcile duplicate records field-by-field" capability the app hasn't needed
yet. Likewise `as_float` (`app/normalization/common.py`) is defined,
exported from neither `__all__`, and unused by any current normalizer (all
duration parsing goes through each platform's bespoke `_parse_duration`
instead).
