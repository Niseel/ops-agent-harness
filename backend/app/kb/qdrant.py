"""Hybrid search on Qdrant: dense and BM25 queries fused with RRF on the client (ADR 0005, 0006, 0007).

The fusion runs here, not in Qdrant, because the tool returns each retriever's
rank. When the query embedding fails or is slower than `kb.embed_timeout_s`, a
`hybrid` search runs BM25 only and reports `sparse_only`.
"""

import asyncio
import logging
from collections.abc import Callable
from dataclasses import dataclass
from functools import cache

from qdrant_client import AsyncQdrantClient, models

from app.config import cfg, settings
from app.kb.sparse import query_vector
from app.llm.openai_compat import OpenAICompatEmbedder

log = logging.getLogger("app.kb")

MODES = ("hybrid", "dense", "sparse")  # dense and sparse exist for evaluation; the tool always asks for hybrid


@dataclass
class Search:
    hits: list[dict]  # fused, best first: {doc_id, title, section, text, score, ranks: {dense, bm25, rrf}}
    mode: str  # hybrid, sparse_only, dense or sparse
    embed_error: str | None  # why a hybrid search ran BM25 only
    dense: list[dict]  # each retriever's ranking: [{rank, doc_id, section, score}]
    bm25: list[dict]


def rrf(rankings: dict[str, list[int]], k: int) -> list[tuple[int, float, dict[str, int | None]]]:
    """Reciprocal rank fusion. `rankings` maps a name to point ids, best first.

    Returns (id, score, {name: rank or None}), best first: by score, then best single rank, then id.
    """
    ids = list(dict.fromkeys(i for ranking in rankings.values() for i in ranking))
    fused = []
    for point_id in ids:
        ranks = {
            name: ranking.index(point_id) + 1 if point_id in ranking else None for name, ranking in rankings.items()
        }
        score = sum(1 / (k + r) for r in ranks.values() if r)
        fused.append((point_id, score, ranks))
    return sorted(fused, key=lambda f: (-f[1], min(r for r in f[2].values() if r), f[0]))


class KnowledgeBase:
    def __init__(self, client: AsyncQdrantClient, embedder) -> None:
        self.client = client
        self.embedder = embedder

    async def search(
        self,
        query: str,
        *,
        mode: str = "hybrid",
        limit: int | None = None,
        fail_embed: Callable[[], bool] | None = None,
    ) -> Search:
        """`fail_embed()` is asked once per hybrid search that would embed; true means an injected failure."""
        if mode not in MODES:
            raise ValueError(f"unknown search mode {mode!r}")
        limit = cfg.kb.top_n if limit is None else limit
        # Read on every search, so an ingest by another process applies at once.
        has_dense = await self._has_dense()
        vector, embed_error = None, None
        if mode == "hybrid" and has_dense:
            if fail_embed is not None and fail_embed():
                embed_error = "injected fault"
            else:
                try:
                    async with asyncio.timeout(cfg.kb.embed_timeout_s):
                        vector = (await self.embedder.embed([query]))[0]
                except Exception as exc:  # the timeout included; CancelledError is not caught
                    embed_error = _reason(exc)
        elif mode == "dense":
            if not has_dense:
                raise LookupError("no dense index: the last ingest ran without embeddings")
            vector = (await self.embedder.embed([query]))[0]

        indices, values = query_vector(query)
        dense_points, bm25_points = await asyncio.gather(
            self._query(vector, "dense", cfg.kb.top_k_dense) if vector is not None else _nothing(),
            self._query(models.SparseVector(indices=indices, values=values), "bm25", cfg.kb.top_k_bm25)
            if mode != "dense" and indices
            else _nothing(),
        )
        payloads = {p.id: p.payload for p in (*dense_points, *bm25_points)}
        fused = rrf({"dense": [p.id for p in dense_points], "bm25": [p.id for p in bm25_points]}, cfg.kb.rrf_k)
        hits = [
            {
                "doc_id": payloads[point_id]["doc_id"],
                "title": payloads[point_id]["title"],
                "section": payloads[point_id]["section"],
                "text": payloads[point_id]["text"],
                "score": score,
                "ranks": {**ranks, "rrf": position},
            }
            for position, (point_id, score, ranks) in enumerate(fused[:limit], start=1)
        ]
        if mode == "hybrid":
            mode = "hybrid" if vector is not None else "sparse_only"
        return Search(hits, mode, embed_error, _ranking(dense_points), _ranking(bm25_points))

    async def status(self) -> str:
        """hybrid, sparse_only or unavailable. It does not probe embeddings."""
        try:
            if not await self.client.collection_exists(cfg.kb.collection):
                return "unavailable"
            return "hybrid" if await self._has_dense() else "sparse_only"
        except Exception:
            return "unavailable"

    async def _has_dense(self) -> bool:
        vectors = (await self.client.get_collection(cfg.kb.collection)).config.params.vectors
        return isinstance(vectors, dict) and "dense" in vectors

    async def _query(self, query, using: str, limit: int) -> list:
        response = await self.client.query_points(
            cfg.kb.collection, query=query, using=using, limit=limit, with_payload=True
        )
        return response.points


async def _nothing() -> list:
    return []


def _ranking(points: list) -> list[dict]:
    return [
        {"rank": i, "doc_id": p.payload["doc_id"], "section": p.payload["section"], "score": p.score}
        for i, p in enumerate(points, start=1)
    ]


def _reason(exc: Exception) -> str:
    if isinstance(exc, TimeoutError):
        return f"no embedding within {cfg.kb.embed_timeout_s} s"
    return f"{type(exc).__name__}: {exc}"[:200]


@cache
def get_kb() -> KnowledgeBase:
    """One knowledge base per process, from settings. Tests replace it with the `kb` fixture."""
    client = AsyncQdrantClient(url=settings.qdrant_url, api_key=settings.qdrant_api_key or None)
    return KnowledgeBase(client, OpenAICompatEmbedder.from_settings())
