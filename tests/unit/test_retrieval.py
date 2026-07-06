"""Unit tests for app/retrieval/service.py's RetrievalService.

Everything that would otherwise hit Supabase/OpenAI is injected via
constructor args (embedding_provider/embedding_repo/post_repo/author_repo/
engagement_repo) or, for the raw Supabase `.table(...).text_search(...)`
chain used directly by `keyword_search`, by monkeypatching
`app.retrieval.service.get_supabase_client`.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

import app.retrieval.service as retrieval_service_module
from app.models.pydantic import Author, Engagement, Post
from app.models.pydantic.enums import ContentType, PlatformName
from app.retrieval.models import RetrievalFilters, RetrievalResult
from app.retrieval.service import RetrievalService
from app.utils.exceptions import RetrievalError

# ============================================================================
# Fakes
# ============================================================================


class _FakeResponse:
    def __init__(self, data):
        self.data = data


class _FakeQueryBuilder:
    def __init__(self, data, *, error=None):
        self._data = data
        self._error = error

    def select(self, *args, **kwargs):
        return self

    def text_search(self, column, query, options=None):
        return self

    def execute(self):
        if self._error:
            raise self._error
        return _FakeResponse(self._data)


class FakeSupabaseClient:
    def __init__(self, data=None, *, error=None):
        self._data = data or []
        self._error = error

    def table(self, name):
        return _FakeQueryBuilder(self._data, error=self._error)


class FakeEmbeddingProvider:
    model_name = "fake-model"
    dimensions = 3

    def __init__(self, vectors=None, error=None):
        self.vectors = vectors if vectors is not None else [[0.1, 0.2, 0.3]]
        self.error = error
        self.calls: list[list[str]] = []

    async def embed_texts(self, texts):
        self.calls.append(texts)
        if self.error:
            raise self.error
        return self.vectors


class FakeEmbeddingRepo:
    def __init__(self, rows=None, error=None):
        self.rows = rows if rows is not None else []
        self.error = error
        self.match_calls: list[dict] = []

    async def match(self, query_vector, *, match_count=10, platform=None):
        self.match_calls.append(
            {"vector": query_vector, "match_count": match_count, "platform": platform}
        )
        if self.error:
            raise self.error
        return self.rows


class FakePostRepo:
    def __init__(self, posts: dict[str, Post] | None = None):
        self.posts = posts or {}
        self.calls: list[str] = []

    async def get_by_id(self, post_id: str) -> Post | None:
        self.calls.append(post_id)
        return self.posts.get(post_id)


class FakeAuthorRepo:
    def __init__(self, authors: dict[str, Author] | None = None):
        self.authors = authors or {}
        self.calls: list[str] = []

    async def get_by_id(self, author_id: str) -> Author | None:
        self.calls.append(author_id)
        return self.authors.get(author_id)


class FakeEngagementRepo:
    def __init__(
        self, engagements: dict[str, Engagement] | None = None, top: list[Engagement] | None = None
    ):
        self.engagements = engagements or {}
        self.top = top or []
        self.calls: list[str] = []

    async def get_by_post(self, post_id: str) -> Engagement | None:
        self.calls.append(post_id)
        return self.engagements.get(post_id)

    async def top_by_likes(self, *, limit: int = 10) -> list[Engagement]:
        return self.top[:limit]


def _make_post(**overrides) -> Post:
    defaults = {
        "platform": PlatformName.INSTAGRAM,
        "platform_post_id": "native-1",
        "author_id": "author-1",
        "content_type": ContentType.POST,
        "caption": "hello world",
    }
    defaults.update(overrides)
    return Post(**defaults)


def _make_service(
    *,
    embedding_provider=None,
    embedding_repo=None,
    post_repo=None,
    author_repo=None,
    engagement_repo=None,
) -> RetrievalService:
    return RetrievalService(
        embedding_provider=embedding_provider or FakeEmbeddingProvider(),
        embedding_repo=embedding_repo or FakeEmbeddingRepo(),
        post_repo=post_repo or FakePostRepo(),
        author_repo=author_repo or FakeAuthorRepo(),
        engagement_repo=engagement_repo or FakeEngagementRepo(),
    )


# ============================================================================
# keyword_search
# ============================================================================


async def test_keyword_search_returns_results_with_keyword_metadata(monkeypatch):
    rows = [
        {"source_type": "post", "source_id": "p1", "platform": "instagram", "content": "hello"},
    ]
    monkeypatch.setattr(
        retrieval_service_module, "get_supabase_client", lambda: FakeSupabaseClient(rows)
    )
    service = _make_service()
    results = await service.keyword_search("hello")

    assert len(results) == 1
    assert results[0].source_id == "p1"
    assert results[0].score == 1.0
    assert results[0].metadata == {"match": "keyword"}


async def test_keyword_search_applies_platform_filter_client_side(monkeypatch):
    rows = [
        {"source_type": "post", "source_id": "p1", "platform": "instagram", "content": "a"},
        {"source_type": "post", "source_id": "p2", "platform": "twitter", "content": "b"},
        {"source_type": "post", "source_id": "p3", "platform": "instagram", "content": "c"},
    ]
    monkeypatch.setattr(
        retrieval_service_module, "get_supabase_client", lambda: FakeSupabaseClient(rows)
    )
    service = _make_service()
    results = await service.keyword_search("x", platform="instagram")

    assert {r.source_id for r in results} == {"p1", "p3"}


async def test_keyword_search_applies_limit_client_side(monkeypatch):
    rows = [
        {"source_type": "post", "source_id": f"p{i}", "platform": "instagram", "content": "x"}
        for i in range(5)
    ]
    monkeypatch.setattr(
        retrieval_service_module, "get_supabase_client", lambda: FakeSupabaseClient(rows)
    )
    service = _make_service()
    results = await service.keyword_search("x", limit=2)

    assert len(results) == 2
    assert [r.source_id for r in results] == ["p0", "p1"]


async def test_keyword_search_wraps_client_error_in_retrieval_error(monkeypatch):
    monkeypatch.setattr(
        retrieval_service_module,
        "get_supabase_client",
        lambda: FakeSupabaseClient(error=RuntimeError("connection lost")),
    )
    service = _make_service()
    with pytest.raises(RetrievalError):
        await service.keyword_search("x")


# ============================================================================
# semantic_search
# ============================================================================


async def test_semantic_search_returns_empty_when_no_vectors_produced():
    embedding_provider = FakeEmbeddingProvider(vectors=[])
    service = _make_service(embedding_provider=embedding_provider)
    results = await service.semantic_search("query text")
    assert results == []


async def test_semantic_search_maps_rows_with_semantic_metadata():
    rows = [
        {
            "source_type": "post",
            "source_id": "p1",
            "platform": "instagram",
            "content": "hi",
            "similarity": 0.87,
        }
    ]
    embedding_repo = FakeEmbeddingRepo(rows=rows)
    service = _make_service(embedding_repo=embedding_repo)
    results = await service.semantic_search("query text", platform="instagram", limit=5)

    assert len(results) == 1
    assert results[0].source_id == "p1"
    assert results[0].score == pytest.approx(0.87)
    assert results[0].metadata == {"match": "semantic"}
    assert embedding_repo.match_calls[0]["match_count"] == 5
    assert embedding_repo.match_calls[0]["platform"] == "instagram"


async def test_semantic_search_wraps_repo_error_in_retrieval_error():
    embedding_repo = FakeEmbeddingRepo(error=RuntimeError("rpc failed"))
    service = _make_service(embedding_repo=embedding_repo)
    with pytest.raises(RetrievalError):
        await service.semantic_search("query text")


# ============================================================================
# hybrid_search
# ============================================================================


async def test_hybrid_search_weights_and_merges_overlapping_results(monkeypatch):
    keyword_rows = [
        {"source_type": "post", "source_id": "p1", "platform": "instagram", "content": "a"},
        {"source_type": "post", "source_id": "p2", "platform": "instagram", "content": "b"},
    ]
    semantic_rows = [
        {
            "source_type": "post",
            "source_id": "p1",
            "platform": "instagram",
            "content": "a",
            "similarity": 0.8,
        },
        {
            "source_type": "post",
            "source_id": "p3",
            "platform": "instagram",
            "content": "c",
            "similarity": 0.5,
        },
    ]
    monkeypatch.setattr(
        retrieval_service_module, "get_supabase_client", lambda: FakeSupabaseClient(keyword_rows)
    )
    embedding_repo = FakeEmbeddingRepo(rows=semantic_rows)
    service = _make_service(embedding_repo=embedding_repo)

    results = await service.hybrid_search("query", limit=10)
    by_id = {r.source_id: r for r in results}

    # p1: keyword (1.0*0.4) + semantic (0.8*0.6) = 0.88, marked hybrid
    assert by_id["p1"].score == pytest.approx(0.4 + 0.8 * 0.6)
    assert by_id["p1"].metadata["match"] == "hybrid"

    # p2: keyword only -> 1.0*0.4
    assert by_id["p2"].score == pytest.approx(0.4)
    assert by_id["p2"].metadata["match"] == "keyword"

    # p3: semantic only -> 0.5*0.6
    assert by_id["p3"].score == pytest.approx(0.5 * 0.6)
    assert by_id["p3"].metadata["match"] == "semantic"


async def test_hybrid_search_sorts_descending_by_score(monkeypatch):
    keyword_rows = [
        {"source_type": "post", "source_id": "low", "platform": "instagram", "content": "a"},
    ]
    semantic_rows = [
        {
            "source_type": "post",
            "source_id": "high",
            "platform": "instagram",
            "content": "b",
            "similarity": 0.99,
        },
    ]
    monkeypatch.setattr(
        retrieval_service_module, "get_supabase_client", lambda: FakeSupabaseClient(keyword_rows)
    )
    embedding_repo = FakeEmbeddingRepo(rows=semantic_rows)
    service = _make_service(embedding_repo=embedding_repo)

    results = await service.hybrid_search("query", limit=10)
    assert [r.source_id for r in results] == ["high", "low"]


async def test_hybrid_search_applies_limit(monkeypatch):
    keyword_rows = [
        {"source_type": "post", "source_id": f"p{i}", "platform": "instagram", "content": "x"}
        for i in range(5)
    ]
    monkeypatch.setattr(
        retrieval_service_module, "get_supabase_client", lambda: FakeSupabaseClient(keyword_rows)
    )
    embedding_repo = FakeEmbeddingRepo(rows=[])
    service = _make_service(embedding_repo=embedding_repo)

    results = await service.hybrid_search("query", limit=2)
    assert len(results) == 2


async def test_hybrid_search_fetches_double_limit_candidates_per_mode(monkeypatch):
    monkeypatch.setattr(
        retrieval_service_module, "get_supabase_client", lambda: FakeSupabaseClient([])
    )
    embedding_repo = FakeEmbeddingRepo(rows=[])
    service = _make_service(embedding_repo=embedding_repo)

    await service.hybrid_search("query", limit=7)
    assert embedding_repo.match_calls[0]["match_count"] == 14


# ============================================================================
# popular_posts
# ============================================================================


async def test_popular_posts_ranks_by_likes_and_maps_metadata():
    post = _make_post(platform=PlatformName.INSTAGRAM)
    engagement = Engagement(post_id="p1", likes=42, views=1000)
    engagement_repo = FakeEngagementRepo(top=[engagement])
    post_repo = FakePostRepo(posts={"p1": post})
    service = _make_service(post_repo=post_repo, engagement_repo=engagement_repo)

    results = await service.popular_posts(limit=10)

    assert len(results) == 1
    assert results[0].source_id == "p1"
    assert results[0].score == 42.0
    assert results[0].metadata["match"] == "popularity"
    assert results[0].metadata["likes"] == 42
    assert results[0].metadata["views"] == 1000


async def test_popular_posts_skips_engagement_without_post_id():
    engagement = Engagement(post_id=None, likes=100)
    engagement_repo = FakeEngagementRepo(top=[engagement])
    service = _make_service(engagement_repo=engagement_repo)

    results = await service.popular_posts(limit=10)
    assert results == []


async def test_popular_posts_skips_when_post_lookup_returns_none():
    engagement = Engagement(post_id="missing-post", likes=100)
    engagement_repo = FakeEngagementRepo(top=[engagement])
    post_repo = FakePostRepo(posts={})
    service = _make_service(post_repo=post_repo, engagement_repo=engagement_repo)

    results = await service.popular_posts(limit=10)
    assert results == []


async def test_popular_posts_filters_by_platform():
    post_ig = _make_post(platform=PlatformName.INSTAGRAM, platform_post_id="ig1")
    post_tw = _make_post(platform=PlatformName.TWITTER, platform_post_id="tw1")
    engagements = [
        Engagement(post_id="p_ig", likes=50),
        Engagement(post_id="p_tw", likes=100),
    ]
    engagement_repo = FakeEngagementRepo(top=engagements)
    post_repo = FakePostRepo(posts={"p_ig": post_ig, "p_tw": post_tw})
    service = _make_service(post_repo=post_repo, engagement_repo=engagement_repo)

    results = await service.popular_posts(platform="instagram", limit=10)
    assert len(results) == 1
    assert results[0].source_id == "p_ig"


async def test_popular_posts_stops_at_limit():
    posts = {f"p{i}": _make_post(platform_post_id=f"native{i}") for i in range(5)}
    engagements = [Engagement(post_id=f"p{i}", likes=100 - i) for i in range(5)]
    engagement_repo = FakeEngagementRepo(top=engagements)
    post_repo = FakePostRepo(posts=posts)
    service = _make_service(post_repo=post_repo, engagement_repo=engagement_repo)

    results = await service.popular_posts(limit=2)
    assert len(results) == 2


# ============================================================================
# _apply_filters
# ============================================================================


def _make_result(source_id="p1", source_type="post") -> RetrievalResult:
    return RetrievalResult(
        source_type=source_type,
        source_id=source_id,
        platform="instagram",
        content="hello",
        score=1.0,
    )


async def test_apply_filters_returns_unchanged_when_no_filters_set():
    post_repo = FakePostRepo()
    service = _make_service(post_repo=post_repo)
    results = [_make_result("p1"), _make_result("p2")]

    filtered = await service._apply_filters(results, RetrievalFilters())

    assert filtered == results
    assert post_repo.calls == []  # no unnecessary lookups when no filters apply


async def test_apply_filters_passes_through_non_post_source_types():
    service = _make_service()
    results = [_make_result("c1", source_type="comment")]
    filters = RetrievalFilters(min_likes=10)

    filtered = await service._apply_filters(results, filters)

    assert filtered == results


async def test_apply_filters_excludes_when_post_lookup_returns_none():
    post_repo = FakePostRepo(posts={})
    service = _make_service(post_repo=post_repo)
    results = [_make_result("missing")]
    filters = RetrievalFilters(min_likes=1)

    filtered = await service._apply_filters(results, filters)
    assert filtered == []


async def test_apply_filters_hashtag_matches():
    post = _make_post(hashtags=["travel", "sunset"])
    post_repo = FakePostRepo(posts={"p1": post})
    service = _make_service(post_repo=post_repo)
    results = [_make_result("p1")]
    filters = RetrievalFilters(hashtag="#Travel")

    filtered = await service._apply_filters(results, filters)
    assert len(filtered) == 1


async def test_apply_filters_hashtag_excludes_non_matching():
    post = _make_post(hashtags=["food"])
    post_repo = FakePostRepo(posts={"p1": post})
    service = _make_service(post_repo=post_repo)
    results = [_make_result("p1")]
    filters = RetrievalFilters(hashtag="travel")

    filtered = await service._apply_filters(results, filters)
    assert filtered == []


async def test_apply_filters_date_range_includes_within_bounds():
    post = _make_post(posted_at=datetime(2024, 6, 15, tzinfo=UTC))
    post_repo = FakePostRepo(posts={"p1": post})
    service = _make_service(post_repo=post_repo)
    results = [_make_result("p1")]
    filters = RetrievalFilters(
        date_from=datetime(2024, 6, 1, tzinfo=UTC), date_to=datetime(2024, 6, 30, tzinfo=UTC)
    )

    filtered = await service._apply_filters(results, filters)
    assert len(filtered) == 1


async def test_apply_filters_date_range_excludes_before_date_from():
    post = _make_post(posted_at=datetime(2024, 5, 1, tzinfo=UTC))
    post_repo = FakePostRepo(posts={"p1": post})
    service = _make_service(post_repo=post_repo)
    results = [_make_result("p1")]
    filters = RetrievalFilters(date_from=datetime(2024, 6, 1, tzinfo=UTC))

    filtered = await service._apply_filters(results, filters)
    assert filtered == []


async def test_apply_filters_date_range_excludes_after_date_to():
    post = _make_post(posted_at=datetime(2024, 7, 1, tzinfo=UTC))
    post_repo = FakePostRepo(posts={"p1": post})
    service = _make_service(post_repo=post_repo)
    results = [_make_result("p1")]
    filters = RetrievalFilters(date_to=datetime(2024, 6, 30, tzinfo=UTC))

    filtered = await service._apply_filters(results, filters)
    assert filtered == []


async def test_apply_filters_date_range_excludes_post_without_posted_at():
    post = _make_post(posted_at=None)
    post_repo = FakePostRepo(posts={"p1": post})
    service = _make_service(post_repo=post_repo)
    results = [_make_result("p1")]
    filters = RetrievalFilters(date_from=datetime(2024, 1, 1, tzinfo=UTC))

    filtered = await service._apply_filters(results, filters)
    assert filtered == []


async def test_apply_filters_author_username_matches():
    post = _make_post(author_id="a1")
    author = Author(platform=PlatformName.INSTAGRAM, platform_user_id="u1", username="alice")
    post_repo = FakePostRepo(posts={"p1": post})
    author_repo = FakeAuthorRepo(authors={"a1": author})
    service = _make_service(post_repo=post_repo, author_repo=author_repo)
    results = [_make_result("p1")]
    filters = RetrievalFilters(author_username="@Alice")

    filtered = await service._apply_filters(results, filters)
    assert len(filtered) == 1


async def test_apply_filters_author_username_excludes_non_matching():
    post = _make_post(author_id="a1")
    author = Author(platform=PlatformName.INSTAGRAM, platform_user_id="u1", username="alice")
    post_repo = FakePostRepo(posts={"p1": post})
    author_repo = FakeAuthorRepo(authors={"a1": author})
    service = _make_service(post_repo=post_repo, author_repo=author_repo)
    results = [_make_result("p1")]
    filters = RetrievalFilters(author_username="bob")

    filtered = await service._apply_filters(results, filters)
    assert filtered == []


async def test_apply_filters_author_username_excludes_when_author_missing():
    post = _make_post(author_id="a1")
    post_repo = FakePostRepo(posts={"p1": post})
    author_repo = FakeAuthorRepo(authors={})
    service = _make_service(post_repo=post_repo, author_repo=author_repo)
    results = [_make_result("p1")]
    filters = RetrievalFilters(author_username="alice")

    filtered = await service._apply_filters(results, filters)
    assert filtered == []


async def test_apply_filters_min_likes_includes_when_above_threshold():
    post = _make_post()
    engagement = Engagement(post_id="p1", likes=100)
    post_repo = FakePostRepo(posts={"p1": post})
    engagement_repo = FakeEngagementRepo(engagements={"p1": engagement})
    service = _make_service(post_repo=post_repo, engagement_repo=engagement_repo)
    results = [_make_result("p1")]
    filters = RetrievalFilters(min_likes=50)

    filtered = await service._apply_filters(results, filters)
    assert len(filtered) == 1


async def test_apply_filters_min_likes_excludes_below_threshold():
    post = _make_post()
    engagement = Engagement(post_id="p1", likes=10)
    post_repo = FakePostRepo(posts={"p1": post})
    engagement_repo = FakeEngagementRepo(engagements={"p1": engagement})
    service = _make_service(post_repo=post_repo, engagement_repo=engagement_repo)
    results = [_make_result("p1")]
    filters = RetrievalFilters(min_likes=50)

    filtered = await service._apply_filters(results, filters)
    assert filtered == []


async def test_apply_filters_min_likes_excludes_when_engagement_missing():
    post = _make_post()
    post_repo = FakePostRepo(posts={"p1": post})
    engagement_repo = FakeEngagementRepo(engagements={})
    service = _make_service(post_repo=post_repo, engagement_repo=engagement_repo)
    results = [_make_result("p1")]
    filters = RetrievalFilters(min_likes=1)

    filtered = await service._apply_filters(results, filters)
    assert filtered == []


async def test_apply_filters_content_types_matches():
    post = _make_post(content_type=ContentType.REEL)
    post_repo = FakePostRepo(posts={"p1": post})
    service = _make_service(post_repo=post_repo)
    results = [_make_result("p1")]
    filters = RetrievalFilters(content_types=["reel", "video"])

    filtered = await service._apply_filters(results, filters)
    assert len(filtered) == 1


async def test_apply_filters_content_types_excludes_non_matching():
    post = _make_post(content_type=ContentType.STORY)
    post_repo = FakePostRepo(posts={"p1": post})
    service = _make_service(post_repo=post_repo)
    results = [_make_result("p1")]
    filters = RetrievalFilters(content_types=["reel", "video"])

    filtered = await service._apply_filters(results, filters)
    assert filtered == []
