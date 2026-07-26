from __future__ import annotations

import logging

from kg.storage.base import Subgraph

log = logging.getLogger(__name__)


def degree_centrality(subgraph: Subgraph) -> dict[str, float]:
    """Normalized degree (0..1) per node, based on edges within the subgraph."""
    deg: dict[str, int] = {n.id: 0 for n in subgraph.nodes}
    for e in subgraph.edges:
        try:
            src, _, tgt = (e.id or "").split("|", 2)
        except ValueError:
            continue
        if src in deg:
            deg[src] += 1
        if tgt in deg:
            deg[tgt] += 1
    mx = max(deg.values(), default=0) or 1
    return {nid: d / mx for nid, d in deg.items()}


def expand(
    adapter,
    seed_ids: list[str],
    hops: int = 2,
    direction: str = "both",
    edge_types: list[str] | None = None,
    cap: int = 300,
) -> Subgraph:
    """BFS-expand from seed_ids via adapter.neighbors, always including seeds,
    excluding tombstoned nodes, and capping node count deterministically by
    keeping the highest-degree nodes (seeds always kept)."""
    seeds = [n for n in (adapter.get(sid) for sid in seed_ids) if n and n.status == "active"]
    seed_id_set = {n.id for n in seeds}

    sg = adapter.neighbors(seed_ids, depth=hops, direction=direction, edge_types=edge_types) if hops > 0 else Subgraph()

    other_nodes = [n for n in sg.nodes if n.status == "active" and n.id not in seed_id_set]
    all_nodes = seeds + other_nodes
    all_ids = {n.id for n in all_nodes}
    edges = [
        e for e in sg.edges
        if (e.id or "").split("|", 2)[0] in all_ids and (e.id or "").split("|", 2)[-1] in all_ids
    ]
    merged = Subgraph(nodes=all_nodes, edges=edges)

    if len(all_nodes) > cap:
        cent = degree_centrality(merged)
        # Seeds win ties; the cap remains strict when callers provide too many.
        ranked = sorted(
            all_nodes,
            key=lambda n: (n.id not in seed_id_set, -cent.get(n.id, 0.0), n.id),
        )
        merged.nodes = ranked[:max(cap, 0)]
        kept_ids = {n.id for n in merged.nodes}
        log.warning(
            "expand: truncated subgraph from %d to %d nodes (cap=%d)",
            len(all_nodes), len(kept_ids), cap,
        )
        merged.edges = [
            e for e in edges
            if (e.id or "").split("|", 2)[0] in kept_ids and (e.id or "").split("|", 2)[-1] in kept_ids
        ]

    return merged
