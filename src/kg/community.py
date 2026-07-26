"""Louvain community detection over graph storage."""
from __future__ import annotations

from kg.storage.base import StorageAdapter

_NODE_CAP = 5000


def louvain(adapter: StorageAdapter, *, seed: int = 42) -> dict[str, int]:
    """Return ``{node_id: cluster_id}`` via Louvain. Deterministic.

    - If active nodes > 5000, returns ``{id: -1}`` for every active node (skip).
    - Falls back to single cluster (all 0) when networkx is unavailable.
    """
    rows = adapter.conn.execute(
        "SELECT id FROM nodes WHERE status='active' ORDER BY id"
    ).fetchall()
    node_ids = [r["id"] for r in rows]
    if len(node_ids) > _NODE_CAP:
        return {nid: -1 for nid in node_ids}

    erows = adapter.conn.execute(
        "SELECT source, target FROM edges WHERE status='active' ORDER BY id"
    ).fetchall()
    active_ids = set(node_ids)
    edges = [(r["source"], r["target"]) for r in erows
             if r["source"] in active_ids and r["target"] in active_ids]

    try:
        import networkx as nx  # type: ignore[import-untyped]
        from networkx.algorithms.community import louvain_communities
    except ImportError:
        return {nid: 0 for nid in node_ids}

    G = nx.Graph()
    G.add_nodes_from(node_ids)
    G.add_edges_from(edges)
    communities = louvain_communities(G, seed=seed)
    mapping: dict[str, int] = {}
    for idx, community in enumerate(communities):
        for nid in community:
            mapping[nid] = idx
    return mapping
