from __future__ import annotations

import logging
from dataclasses import dataclass

from kg.storage.base import Subgraph

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class ExpandResult:
    """Result of budget-aware graph expansion."""
    subgraph: Subgraph
    """Expanded nodes/edges (possibly truncated by budget)."""

    truncated: bool
    """True if traversal work budget (`max_nodes`) was hit."""

    completed_hops: int
    """Number of fully completed hops (≤ requested hops)."""

    nodes_examined: int
    """Total neighbor nodes the SQL CTE examined before hitting the budget."""


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
    max_nodes: int = 500,
) -> Subgraph:
    """BFS-expand from seed_ids via adapter.neighbors, always including seeds,
    excluding tombstoned nodes, capping node count deterministically by
    keeping the highest-degree nodes (seeds always kept). `cap` is a
    presentation cap; `max_nodes` is a work budget propagated into the SQL
    CTE so dense graphs cannot explode before we hit `cap`."""
    return expand_with_meta(
        adapter,
        seed_ids,
        hops=hops,
        direction=direction,
        edge_types=edge_types,
        cap=cap,
        max_nodes=max_nodes,
    ).subgraph


def expand_with_meta(
    adapter,
    seed_ids: list[str],
    hops: int = 2,
    direction: str = "both",
    edge_types: list[str] | None = None,
    cap: int = 300,
    max_nodes: int = 500,
) -> ExpandResult:
    """Budget-aware BFS expand. Returns ``ExpandResult`` with truncation
    metadata plus the deterministic presentation-cap output. Seeds always
    survive; presentation cap keeps highest-degree nodes first."""
    seeds = [n for n in (adapter.get(sid) for sid in seed_ids) if n and n.status == "active"]
    seed_id_set = {n.id for n in seeds}
    truncated = False
    nodes_examined = 0

    if hops > 0 and seed_id_set:
        # Probe the adapter for the work budget's outcome first, then call
        # neighbors once more. This avoids threading two distinct budgets
        # through the adapter; we infer `truncated` from row count.
        pre_sg = adapter.neighbors(
            seed_ids,
            depth=hops,
            direction=direction,
            edge_types=edge_types,
            max_nodes=max_nodes,
        )
        sg = pre_sg
    else:
        sg = Subgraph()

    other_nodes = [n for n in sg.nodes if n.status == "active" and n.id not in seed_id_set]
    all_nodes = seeds + other_nodes
    all_ids = {n.id for n in all_nodes}
    edges = [
        e for e in sg.edges
        if (e.id or "").split("|", 2)[0] in all_ids and (e.id or "").split("|", 2)[-1] in all_ids
    ]

    # The SQL CTE deduplicates within its own `max_nodes + len(ids)` row
    # budget. When the result contains the max distinct neighbors we asked
    # for, treat the budget as a constraint rather than an unknown — assume
    # there is more graph left and mark truncated.
    if hops > 0 and seed_id_set and len(other_nodes) >= max_nodes:
        truncated = True
        nodes_examined = len(other_nodes)
    else:
        nodes_examined = len(other_nodes)

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

    completed_hops = hops if seed_id_set and not truncated else int(bool(other_nodes))
    return ExpandResult(
        subgraph=merged,
        truncated=truncated,
        completed_hops=completed_hops,
        nodes_examined=nodes_examined,
    )
