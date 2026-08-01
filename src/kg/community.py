"""Louvain community detection over graph storage."""
from __future__ import annotations

import logging

from kg.storage.base import StorageAdapter

log = logging.getLogger(__name__)

_NODE_CAP = 5000


def louvain(adapter: StorageAdapter, *, seed: int = 42) -> dict[str, int]:
    """Return ``{node_id: cluster_id}`` via Louvain. Deterministic.

    - If active nodes > 5000, runs Louvain per connected component and unions
      results with offset cluster ids (handles 10k+ node graphs).
    - Falls back to single cluster (all 0) when networkx is unavailable.
    """
    rows = adapter.conn.execute(
        "SELECT id FROM nodes WHERE status='active' ORDER BY id"
    ).fetchall()
    node_ids = [r["id"] for r in rows]
    if not node_ids:
        return {}

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

    # Fast path: small graph — single Louvain run.
    if len(node_ids) <= _NODE_CAP:
        communities = louvain_communities(G, seed=seed)
        mapping: dict[str, int] = {}
        for idx, community in enumerate(communities):
            for nid in community:
                mapping[nid] = idx
        return mapping

    # Scale path: per-component Louvain, offset cluster ids.
    # Real-world graphs are rarely one 10k-component; splitting keeps each
    # Louvain run under the cap while still detecting real communities.
    log.info("louvain: %d nodes > cap %d; running per connected component",
             len(node_ids), _NODE_CAP)
    mapping = {}
    cluster_offset = 0
    for component in nx.connected_components(G):
        comp_nodes = list(component)
        if len(comp_nodes) == 1:
            mapping[comp_nodes[0]] = cluster_offset
            cluster_offset += 1
            continue
        sub = G.subgraph(comp_nodes)
        communities = louvain_communities(sub, seed=seed)
        for community in communities:
            for nid in community:
                mapping[nid] = cluster_offset
            cluster_offset += 1
    # orphans (no edges) not in any component loop — assign singleton clusters
    for nid in node_ids:
        if nid not in mapping:
            mapping[nid] = cluster_offset
            cluster_offset += 1
    return mapping


def louvain_cached(adapter: StorageAdapter) -> dict[str, int]:
    """Return materialized Louvain communities for the current generation."""
    generation = adapter.generation()  # type: ignore[attr-defined]
    cached = adapter.get_clusters(generation)  # type: ignore[attr-defined]
    if cached is not None:
        return cached
    mapping = louvain(adapter)
    if mapping:
        adapter.store_clusters(mapping, generation)  # type: ignore[attr-defined]
    return mapping
