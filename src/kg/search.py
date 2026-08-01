from __future__ import annotations

from kg.embed import Embedder
from kg.storage.base import StorageAdapter, Subgraph
from kg.traverse import expand

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


def weighted_rrf(
    streams: list[tuple[list[str], float]],
    k: int = 60,
) -> list[tuple[str, float]]:
    """RRF with per-stream weights. Score += weight * 1/(k + rank + 1)."""
    scores: dict[str, float] = {}
    for rank_list, weight in streams:
        for rank, item in enumerate(rank_list):
            scores[item] = scores.get(item, 0.0) + weight * 1.0 / (k + rank + 1)
    return sorted(scores.items(), key=lambda kv: -kv[1])


def graph_aware_hybrid_search(
    adapter: StorageAdapter,
    embedder: Embedder,
    query: str,
    policy,  # duck-typed: .hops, .cap, .weights dict
    *,
    config=None,
    return_subgraph: bool = False,
) -> list[tuple[str, float]] | tuple[list[tuple[str, float]], Subgraph | None]:
    """3-stream RRF: BM25 + vector + graph expansion from top seeds.

    If ``return_subgraph`` is True, returns a tuple ``(ranked_ids, subgraph)``.
    The ``subgraph`` is the already-expanded graph used for the graph stream,
    so callers can reuse it instead of calling ``expand()`` again on the same
    seeds. If no expansion was performed, ``subgraph`` is ``None``.
    """
    cap = policy.cap
    bm25_hits = adapter.fts_search(query, k=cap * 3, type_filter=None)
    vec_hits = adapter.vec_search(embedder.embed(query), k=cap * 3, type_filter=None)

    bm25_ids = [nid for nid, _ in bm25_hits]
    vec_ids = [nid for nid, _ in vec_hits]

    seeds = []
    seen = set()
    for nid in bm25_ids[:10] + vec_ids[:10]:
        if nid not in seen:
            seeds.append(nid)
            seen.add(nid)

    graph_ids: list[str] = []
    sg = None
    if policy.hops > 0 and seeds:
        max_nodes = 500
        if config is not None and getattr(config, "query", None) is not None:
            max_nodes = getattr(config.query, "traverse_budget", max_nodes)
        sg = expand(adapter, seeds, hops=policy.hops, cap=cap, max_nodes=max_nodes)
        graph_ids = [n.id for n in sg.nodes]

    streams = [
        (bm25_ids, policy.weights["lexical"]),
        (vec_ids, policy.weights["semantic"]),
        (graph_ids, policy.weights["graph"]),
    ]

    rrf_k = 60
    if config is not None and getattr(config, "query", None) is not None:
        rrf_k = getattr(config.query, "rrf_k", rrf_k)

    ranked = weighted_rrf(streams, k=rrf_k)[:cap]
    if return_subgraph:
        return ranked, sg
    return ranked
