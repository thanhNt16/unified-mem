"""Tests for budget-aware graph traversal (traverse_budget)."""
from __future__ import annotations

from time import monotonic

import pytest

from kg.ontology import Edge, Node
from kg.storage.sqlite import SQLiteAdapter
from kg.traverse import expand, expand_with_meta


def test_expand_respects_budget_on_adversarial_hub(tmp_path):
    """Dense hub graph must not materialize unbounded paths.

    Setup: one hub + 1000 leaf nodes (avg degree 1000). Depth=3 would
    explode without a budget. Traversal must stop at budget and stay
    fast (<1s).
    """
    a = SQLiteAdapter(tmp_path / "kg.db")
    hub = "u:object:hub"
    leaves = [f"u:object:leaf_{i}" for i in range(1000)]
    nodes = [Node(id=hub, type="object", name="hub")] + [
        Node(id=leaf, type="object", name=leaf) for leaf in leaves
    ]
    a.upsert_nodes(nodes)
    edges = [
        Edge(id=f"{hub}|related_to|{leaf}", semantic_type="related_to")
        for leaf in leaves
    ]
    a.upsert_edges(edges)

    # Budget of 200 should not explode on 100k potential paths.
    started = monotonic()
    res = expand(a, [hub], hops=3, cap=300, max_nodes=200)
    elapsed = monotonic() - started
    # We must get a bounded subgraph: seeds + up to budget neighbors.
    assert len(res.nodes) <= 201
    assert elapsed < 1
    assert hub in {n.id for n in res.nodes}


def test_expand_with_meta_reports_truncation(tmp_path):
    """ExpandResult meta flags must expose budget exhaustion."""
    a = SQLiteAdapter(tmp_path / "kg.db")
    hub = "u:object:hub"
    leaves = [f"u:object:{i}" for i in range(200)]
    nodes = [Node(id=hub, type="object", name="hub")] + [
        Node(id=leaf, type="object", name=leaf) for leaf in leaves
    ]
    a.upsert_nodes(nodes)
    edges = [
        Edge(id=f"{hub}|related_to|{leaf}", semantic_type="related_to")
        for leaf in leaves
    ]
    a.upsert_edges(edges)

    # Budget lower than leaf count triggers truncation.
    res = expand_with_meta(a, [hub], hops=1, cap=300, max_nodes=50)
    assert res.truncated
    assert res.nodes_examined >= 50
    # completed_hops=1 because we did reach neighbors
    assert res.completed_hops == 1


def test_expand_with_meta_full_subgraph_below_budget(tmp_path):
    """When budget exceeds reachable nodes, everything is returned and truncated=False."""
    a = SQLiteAdapter(tmp_path / "kg.db")
    nodes = [Node(id=f"u:object:{i}", type="object", name=str(i)) for i in "abcdef"]
    a.upsert_nodes(nodes)
    edges = [
        Edge(id="u:object:a|related_to|u:object:b", semantic_type="related_to"),
        Edge(id="u:object:b|related_to|u:object:c", semantic_type="related_to"),
        Edge(id="u:object:c|related_to|u:object:d", semantic_type="related_to"),
    ]
    a.upsert_edges(edges)

    res = expand_with_meta(a, ["u:object:a"], hops=2, cap=300, max_nodes=100)
    assert not res.truncated
    assert {n.id for n in res.subgraph.nodes} >= {"u:object:a", "u:object:b", "u:object:c"}
    assert res.completed_hops == 2
