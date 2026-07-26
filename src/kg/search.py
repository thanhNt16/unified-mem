from __future__ import annotations

from kg.embed import Embedder
from kg.storage.base import StorageAdapter

_VALID_MODES = ("hybrid", "bm25", "semantic", "keyword")


def rrf(rank_lists: list[list[str]], k: int = 60) -> list[tuple[str, float]]:
    scores: dict[str, float] = {}
    for ranks in rank_lists:
        for rank, item in enumerate(ranks):
            scores[item] = scores.get(item, 0.0) + 1.0 / (k + rank + 1)
    return sorted(scores.items(), key=lambda kv: -kv[1])


def hybrid_search(
    adapter: StorageAdapter,
    embedder: Embedder,
    query: str,
    mode: str = "hybrid",
    k: int = 10,
    type_filter: str | None = None,
    config=None,
) -> list[tuple[str, float]]:
    if mode not in _VALID_MODES:
        raise ValueError(f"invalid mode {mode!r}; expected one of {_VALID_MODES}")

    rank_lists: list[list[str]] = []
    if mode in ("hybrid", "bm25", "keyword"):
        fts = adapter.fts_search(query, k=k * 5, type_filter=type_filter)
        rank_lists.append([node_id for node_id, _ in fts])
    if mode in ("hybrid", "semantic"):
        qv = embedder.embed(query)
        vec = adapter.vec_search(qv, k=k * 5, type_filter=type_filter)
        rank_lists.append([node_id for node_id, _ in vec])

    rrf_k = 60
    if config is not None and getattr(config, "query", None) is not None:
        rrf_k = getattr(config.query, "rrf_k", rrf_k)

    fused = rrf(rank_lists, k=rrf_k)
    return fused[:k]
