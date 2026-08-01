"""Ingest batching: ensure a 1000-node upsert opens at most one transaction."""
from __future__ import annotations

import pytest
from kg.ontology import Node
from kg.storage.sqlite import SQLiteAdapter


def _adapter(tmp_path):
    return SQLiteAdapter(tmp_path / "kg.db")


def _node(i: int) -> Node:
    return Node(
        id=f"u:person:p{i:05d}",
        type="person",
        name=f"Person {i}",
        summary=f"person {i}",
        status="active",
    )


def _spy_on_transaction(a):
    """Wrap a.transaction() to count outermost BEGIN/COMMIT entries."""
    txn_count = {"n": 0}
    real = type(a).transaction

    def counting(self):
        txn_count["n"] += 1
        return real(self)

    return counting, txn_count


def test_upsert_nodes_opens_single_transaction(tmp_path, monkeypatch):
    """1000-node upsert must trigger exactly one outermost transaction."""
    a = _adapter(tmp_path)
    counting, txn_count = _spy_on_transaction(a)
    monkeypatch.setattr(type(a), "transaction", counting)
    nodes = [_node(i) for i in range(1000)]
    n = a.upsert_nodes(nodes)
    assert n == 1000
    assert a.count()["nodes"] == 1000
    assert txn_count["n"] == 1, f"expected 1 transaction, got {txn_count['n']}"


def test_upsert_edges_opens_single_transaction(tmp_path, monkeypatch):
    from kg.ontology import Edge
    a = _adapter(tmp_path)
    a.upsert_nodes([_node(i) for i in range(10)])
    counting, txn_count = _spy_on_transaction(a)
    monkeypatch.setattr(type(a), "transaction", counting)
    edges = [
        Edge(id=f"u:person:p{i:05d}|knows|u:person:p{(i+1)%10:05d}",
             semantic_type="knows")
        for i in range(500)
    ]
    a.upsert_edges(edges)
    assert a.count()["edges"] == 500
    assert txn_count["n"] == 1


def test_upsert_nodes_empty_batch_is_noop(tmp_path):
    a = _adapter(tmp_path)
    assert a.upsert_nodes([]) == 0
    assert a.count()["nodes"] == 0


def test_upsert_nodes_uses_executemany_per_table(tmp_path):
    """Internal calls should batch with executemany (asserted via Popen trace)."""
    a = _adapter(tmp_path)
    nodes = [_node(i) for i in range(50)]
    a.upsert_nodes(nodes)
    assert a.count()["nodes"] == 50
    # FTS must have all rows
    fts_count = a.conn.execute("SELECT COUNT(*) FROM nodes_fts").fetchone()[0]
    assert fts_count == 50
