from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from kg.ontology import Node, Edge
from kg.storage.sqlite import SQLiteAdapter
from kg.pack import diversify_by_source, adaptive_budget


def make_node(adapter: SQLiteAdapter, name: str, src_doc: str) -> str:
    n = Node(
        id=f"u:person:{name}",
        type="person",
        name=name,
        sources=[{"doc": src_doc, "chunk": 0}],
        status="active",
    )
    adapter.upsert_nodes([n])
    return n.id


def test_diversify_by_source_caps_per_source():
    """5 nodes from docA, 2 from docB, cap=3 → 3 from docA + 2 from docB = 5 total"""
    with tempfile.TemporaryDirectory() as td:
        db = Path(td) / "kg.db"
        adapter = SQLiteAdapter(db)
        ids = []
        for i in range(5):
            ids.append(make_node(adapter, f"alice{i}", "docA"))
        for i in range(2):
            ids.append(make_node(adapter, f"bob{i}", "docB"))
        ranked = [(id_, 1.0 - i * 0.01) for i, id_ in enumerate(ids)]
        result = diversify_by_source(ranked, adapter, max_per_source=3)
        assert len(result) == 5
        src_counts = {}
        for node_id, _ in result:
            n = adapter.get(node_id)
            doc = n.sources[0]["doc"] if n.sources else "unknown"
            src_counts[doc] = src_counts.get(doc, 0) + 1
        assert src_counts["docA"] == 3
        assert src_counts["docB"] == 2


def test_diversify_by_source_unknown_grouped():
    """node with no sources → grouped as 'unknown'"""
    with tempfile.TemporaryDirectory() as td:
        db = Path(td) / "kg.db"
        adapter = SQLiteAdapter(db)
        n1 = Node(id="u:p:a", type="person", name="A", sources=[], status="active")
        n2 = Node(id="u:p:b", type="person", name="B", sources=[], status="active")
        adapter.upsert_nodes([n1, n2])
        ranked = [(n1.id, 1.0), (n2.id, 0.9)]
        result = diversify_by_source(ranked, adapter, max_per_source=3)
        assert len(result) == 2
        src_counts = {}
        for node_id, _ in result:
            n = adapter.get(node_id)
            doc = n.sources[0]["doc"] if n.sources else "unknown"
            src_counts[doc] = src_counts.get(doc, 0) + 1
        assert src_counts["unknown"] == 2


def test_diversify_by_source_skips_unknown_ids():
    """unknown node_id in ranked → skipped, no error"""
    with tempfile.TemporaryDirectory() as td:
        db = Path(td) / "kg.db"
        adapter = SQLiteAdapter(db)
        ids = [make_node(adapter, "alice", "docA")]
        ranked = [(ids[0], 1.0), ("u:missing", 0.5)]
        result = diversify_by_source(ranked, adapter, max_per_source=3)
        assert len(result) == 1
        assert result[0][0] == ids[0]


def test_adaptive_budget_sparse_density():
    """density < 1.0 (0 edges, 5 nodes) → scale 1.2"""
    with tempfile.TemporaryDirectory() as td:
        db = Path(td) / "kg.db"
        adapter = SQLiteAdapter(db)
        for i in range(5):
            make_node(adapter, f"p{i}", "docA")
        result = adaptive_budget(4000, adapter)
        assert result == int(4000 * 1.2)


def test_adaptive_budget_dense_density():
    """density > 3.0 (many edges) → scale 0.7"""
    with tempfile.TemporaryDirectory() as td:
        db = Path(td) / "kg.db"
        adapter = SQLiteAdapter(db)
        # 4 nodes, multiple edge types per pair to push density > 3.0.
        # 4 nodes × 4 edges each (knows/related_to/employed_by/uses) = 16 → density 4.0
        ids = [make_node(adapter, f"p{i}", "docA") for i in range(4)]
        edge_types = ["knows", "related_to", "employed_by", "uses"]
        for i, src in enumerate(ids):
            for tgt in ids[i+1:]:
                for et in edge_types:
                    adapter.conn.execute(
                        "INSERT INTO edges (id, source, target, semantic_type, data, status) "
                        "VALUES (?,?,?,?,?,?)",
                        (f"{src}|{et}|{tgt}", src, tgt, et, "{}", "active")
                    )
        adapter.conn.commit()
        result = adaptive_budget(4000, adapter)
        assert result == int(4000 * 0.7)


def test_adaptive_budget_empty_graph():
    """n=0 → returns config_budget unchanged"""
    with tempfile.TemporaryDirectory() as td:
        db = Path(td) / "kg.db"
        adapter = SQLiteAdapter(db)
        result = adaptive_budget(4000, adapter)
        assert result == 4000


def test_adaptive_budget_mid_density():
    """density 1-3 → scale 1.0"""
    with tempfile.TemporaryDirectory() as td:
        db = Path(td) / "kg.db"
        adapter = SQLiteAdapter(db)
        ids = [make_node(adapter, f"q{i}", "docA") for i in range(4)]
        adapter.conn.execute(
            "INSERT INTO edges (id, source, target, semantic_type, data, status) "
            "VALUES (?,?,?,?,?,?)",
            (f"{ids[0]}|knows|{ids[1]}", ids[0], ids[1], "knows", "{}", "active")
        )
        adapter.conn.execute(
            "INSERT INTO edges (id, source, target, semantic_type, data, status) "
            "VALUES (?,?,?,?,?,?)",
            (f"{ids[1]}|knows|{ids[2]}", ids[1], ids[2], "knows", "{}", "active")
        )
        adapter.conn.execute(
            "INSERT INTO edges (id, source, target, semantic_type, data, status) "
            "VALUES (?,?,?,?,?,?)",
            (f"{ids[2]}|knows|{ids[3]}", ids[2], ids[3], "knows", "{}", "active")
        )
        adapter.conn.execute(
            "INSERT INTO edges (id, source, target, semantic_type, data, status) "
            "VALUES (?,?,?,?,?,?)",
            (f"{ids[3]}|knows|{ids[0]}", ids[3], ids[0], "knows", "{}", "active")
        )
        adapter.conn.commit()
        result = adaptive_budget(4000, adapter)
        assert result == 4000
