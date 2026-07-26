from __future__ import annotations
import logging

import pytest

from kg.ontology import Edge, Node
from kg.storage.sqlite import SQLiteAdapter
from kg.traverse import degree_centrality, expand


def test_expand_two_hop(tmp_path):
    a = SQLiteAdapter(tmp_path / "kg.db")
    a.upsert_nodes([Node(id=f"u:object:{i}", type="object", name=str(i)) for i in "abc"])
    a.upsert_edges([
        Edge(id="u:object:a|related_to|u:object:b", semantic_type="related_to"),
        Edge(id="u:object:b|related_to|u:object:c", semantic_type="related_to"),
    ])
    sg = expand(a, ["u:object:a"], hops=2)
    assert {"u:object:a", "u:object:b", "u:object:c"} <= {n.id for n in sg.nodes}


def test_degree_centrality(tmp_path):
    a = SQLiteAdapter(tmp_path / "kg.db")
    a.upsert_nodes([Node(id=f"u:object:{i}", type="object", name=i) for i in "abc"])
    a.upsert_edges([
        Edge(id="u:object:a|related_to|u:object:b", semantic_type="related_to"),
        Edge(id="u:object:a|related_to|u:object:c", semantic_type="related_to"),
    ])
    sg = a.neighbors(["u:object:a", "u:object:b", "u:object:c"], depth=1)
    cent = degree_centrality(sg)
    assert cent["u:object:a"] >= cent["u:object:b"]


def test_expand_respects_cap_deterministically(tmp_path, caplog):
    a = SQLiteAdapter(tmp_path / "kg.db")
    hub, leaves = "u:object:hub", [f"u:object:{i}" for i in "abcd"]
    a.upsert_nodes([Node(id=hub, type="object", name="hub")] + [
        Node(id=node_id, type="object", name=node_id) for node_id in leaves
    ])
    a.upsert_edges([
        Edge(id=f"{hub}|related_to|{leaf}", semantic_type="related_to")
        for leaf in leaves
    ])
    with caplog.at_level(logging.WARNING):
        sg = expand(a, [hub], hops=1, cap=2)
    assert len(sg.nodes) <= 2
    assert hub in {n.id for n in sg.nodes}
    assert "truncated" in caplog.text.lower()


def test_expand_depth_zero_returns_seed_nodes(tmp_path):
    a = SQLiteAdapter(tmp_path / "kg.db")
    a.upsert_nodes([Node(id=f"u:object:{i}", type="object", name=str(i)) for i in "ab"])
    sg = expand(a, ["u:object:a"], hops=0)
    assert {n.id for n in sg.nodes} == {"u:object:a"}


def test_expand_excludes_tombstoned_nodes(tmp_path):
    a = SQLiteAdapter(tmp_path / "kg.db")
    a.upsert_nodes([
        Node(id="u:object:a", type="object", name="a"),
        Node(id="u:object:b", type="object", name="b"),
        Node(id="u:object:c", type="object", name="c", status="tombstoned"),
    ])
    a.upsert_edges([
        Edge(id="u:object:a|related_to|u:object:b", semantic_type="related_to"),
        Edge(id="u:object:a|related_to|u:object:c", semantic_type="related_to"),
    ])
    sg = expand(a, ["u:object:a"], hops=1)
    assert {n.id for n in sg.nodes} == {"u:object:a", "u:object:b"}
