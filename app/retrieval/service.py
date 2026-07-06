"""Hybrid retrieval: keyword search (Postgres `tsvector`), semantic search
(pgvector cosine similarity via the `match_embeddings` RPC), and metadata
filtering (platform/author/hashtag/date/popularity), combinable.

This is the one place the AI assistant goes to fetch "relevant records" —
it never queries `documents`/`embeddings`/`posts` directly.
"""

from __future__ import annotations

import asyncio
from typing import Any

from app.database.supabase_client import get_supabase_client
from app.embeddings.providers import EmbeddingProvider, OpenAIEmbeddingProvider
from app.logging import get_logger
from app.repositories.author_repository import AuthorRepository
from app.repositories.embedding_repository import EmbeddingRepository
from app.repositories.engagement_repository import EngagementRepository
from app.repositories.post_repository import PostRepository
from app.retrieval.models import RetrievalFilters, RetrievalResult
from app.utils.exceptions import RetrievalError

logger = get_logger(__name__)

# Weights for combining keyword/semantic scores in hybrid mode. Semantic
# similarity is weighted higher because it degrades more gracefully on
# paraphrased queries than plain keyword matching.
_KEYWORD_WEIGHT = 0.4
_SEMANTIC_WEIGHT = 0.6


class RetrievalService:
    def __init__(
        self,
        embedding_provider: EmbeddingProvider | None = None,
        embedding_repo: EmbeddingRepository | None = None,
        post_repo: PostRepository | None = None,
        author_repo: AuthorRepository | None = None,
        engagement_repo: EngagementRepository | None = None,
    ) -> None:
        self.embedding_provider = embedding_provider or OpenAIEmbeddingProvider()
        self.embedding_repo = embedding_repo or EmbeddingRepository()
        self.post_repo = post_repo or PostRepository()
        self.author_repo = author_repo or AuthorRepository()
        self.engagement_repo = engagement_repo or EngagementRepository()

    async def keyword_search(
        self, query: str, *, platform: str | None = None, limit: int = 20
    ) -> list[RetrievalResult]:
        """Full-text search over `documents.search_vector`.

        `text_search()` (postgrest-py) returns a stripped-down request
        builder that no longer exposes `.eq()`/`.limit()` for further
        chaining, so platform filtering and the result cap are applied in
        Python after the (already-indexed, GIN) full-text match runs.

        `options={"type": "web_search"}` maps to Postgres's
        `websearch_to_tsquery`, which accepts arbitrary natural-language
        input (spaces, punctuation, a full question). The default
        (`to_tsquery` syntax) requires `word1 & word2`-style boolean
        operators and raises a syntax error on a plain question like
        "What has NASA posted about recently?".
        """

        def _run() -> Any:
            return (
                get_supabase_client()
                .table("documents")
                .select("*")
                .text_search("search_vector", query, options={"type": "web_search"})
                .execute()
            )

        try:
            response = await asyncio.to_thread(_run)
        except Exception as exc:
            raise RetrievalError(f"Keyword search failed: {exc}") from exc

        rows = response.data
        if platform:
            rows = [row for row in rows if row["platform"] == platform]

        return [
            RetrievalResult(
                source_type=row["source_type"],
                source_id=row["source_id"],
                platform=row["platform"],
                content=row["content"],
                score=1.0,
                metadata={"match": "keyword"},
            )
            for row in rows[:limit]
        ]

    async def semantic_search(
        self, query: str, *, platform: str | None = None, limit: int = 20
    ) -> list[RetrievalResult]:
        """Cosine-similarity search over `embeddings` via `match_embeddings`."""
        vectors = await self.embedding_provider.embed_texts([query])
        if not vectors:
            return []
        try:
            rows = await self.embedding_repo.match(vectors[0], match_count=limit, platform=platform)
        except Exception as exc:
            raise RetrievalError(f"Semantic search failed: {exc}") from exc

        return [
            RetrievalResult(
                source_type=row["source_type"],
                source_id=row["source_id"],
                platform=row["platform"],
                content=row["content"],
                score=float(row["similarity"]),
                metadata={"match": "semantic"},
            )
            for row in rows
        ]

    async def hybrid_search(
        self,
        query: str,
        filters: RetrievalFilters | None = None,
        *,
        limit: int = 10,
    ) -> list[RetrievalResult]:
        """Keyword + semantic search, merged, filtered, and ranked.

        Fetches `limit * 2` candidates per mode before filtering so that
        metadata filters (which apply after retrieval) don't starve the
        final result set.
        """
        filters = filters or RetrievalFilters()
        keyword_results, semantic_results = await asyncio.gather(
            self.keyword_search(query, platform=filters.platform, limit=limit * 2),
            self.semantic_search(query, platform=filters.platform, limit=limit * 2),
        )

        merged: dict[tuple[str, str], RetrievalResult] = {}
        for result in keyword_results:
            merged[result.key] = RetrievalResult(
                source_type=result.source_type,
                source_id=result.source_id,
                platform=result.platform,
                content=result.content,
                score=result.score * _KEYWORD_WEIGHT,
                metadata={**result.metadata},
            )
        for result in semantic_results:
            if result.key in merged:
                existing = merged[result.key]
                existing.score += result.score * _SEMANTIC_WEIGHT
                existing.metadata["match"] = "hybrid"
            else:
                merged[result.key] = RetrievalResult(
                    source_type=result.source_type,
                    source_id=result.source_id,
                    platform=result.platform,
                    content=result.content,
                    score=result.score * _SEMANTIC_WEIGHT,
                    metadata={**result.metadata},
                )

        results = await self._apply_filters(list(merged.values()), filters)
        results.sort(key=lambda r: r.score, reverse=True)
        return results[:limit]

    async def popular_posts(
        self, *, platform: str | None = None, limit: int = 10
    ) -> list[RetrievalResult]:
        """Direct popularity ranking, bypassing text/vector search entirely —
        backs questions like "most liked posts this month" where there's no
        keyword/semantic query, only a sort + filter.
        """
        engagements = await self.engagement_repo.top_by_likes(limit=limit * 3)
        results: list[RetrievalResult] = []
        for engagement in engagements:
            if engagement.post_id is None:
                continue
            post = await self.post_repo.get_by_id(engagement.post_id)
            if post is None:
                continue
            if platform and post.platform != platform:
                continue
            results.append(
                RetrievalResult(
                    source_type="post",
                    source_id=engagement.post_id,
                    platform=post.platform,
                    content=post.caption or post.content or "",
                    score=float(engagement.likes or 0),
                    metadata={
                        "match": "popularity",
                        "likes": engagement.likes,
                        "views": engagement.views,
                    },
                )
            )
            if len(results) >= limit:
                break
        return results

    async def _apply_filters(
        self, results: list[RetrievalResult], filters: RetrievalFilters
    ) -> list[RetrievalResult]:
        if not any(
            [
                filters.author_username,
                filters.hashtag,
                filters.date_from,
                filters.date_to,
                filters.min_likes,
                filters.content_types,
            ]
        ):
            return results

        filtered: list[RetrievalResult] = []
        for result in results:
            if result.source_type != "post":
                filtered.append(result)
                continue
            post = await self.post_repo.get_by_id(result.source_id)
            if post is None:
                continue
            if filters.content_types and post.content_type not in filters.content_types:
                continue
            if filters.hashtag and filters.hashtag.lstrip("#").lower() not in post.hashtags:
                continue
            if filters.date_from and (not post.posted_at or post.posted_at < filters.date_from):
                continue
            if filters.date_to and (not post.posted_at or post.posted_at > filters.date_to):
                continue
            if filters.author_username:
                author = await self.author_repo.get_by_id(post.author_id)
                if not author or author.username != filters.author_username.lstrip("@").lower():
                    continue
            if filters.min_likes is not None:
                engagement = await self.engagement_repo.get_by_post(result.source_id)
                if not engagement or (engagement.likes or 0) < filters.min_likes:
                    continue
            filtered.append(result)
        return filtered
