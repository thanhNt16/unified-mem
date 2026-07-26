import json

import pytest

from kg.config import Config
from kg.dedup import Deduper
from kg.embed import FakeEmbedder
from kg.gate import Gate
from kg.ontology import Edge, Node
from kg.resolve import Resolver
from kg.storage.sqlite import SQLiteAdapter


def _gate(tmp_path):
    adapter = SQLiteAdapter(tmp_path / "kg.db")
    cfg = Config.default()
    emb = FakeEmbedder()
    return adapter, Gate(
        adapter, Resolver(adapter, emb, cfg.thresholds),
        Deduper(adapter, emb, cfg.thresholds), emb, cfg, "u",
    )


def _seed(adapter, *, edge_status="pending", winner_type="person",
          loser_type="person", loser_status="active", self_edge=False):
    winner = Node(id="u:person:w", type=winner_type, name="Winner", summary="short")
    loser = Node(
        id="u:person:l", type=loser_type, name="Loser", summary="longer summary",
        status=loser_status,
    )
    adapter.upsert_nodes([winner, loser])
    target = winner.id if self_edge else loser.id
    review = Edge(
        id=f"{winner.id}|same_as|{target}", semantic_type="same_as",
        status=edge_status, confidence=0.9, summary="candidate",
    )
    adapter.upsert_edges([review])
    return winner, loser, review


def _snapshot(adapter):
    return [tuple(r) for r in adapter.conn.execute(
        "SELECT id, data, status FROM nodes ORDER BY id"
    )], [tuple(r) for r in adapter.conn.execute(
        "SELECT id, data, status FROM edges ORDER BY id"
    )]


def test_review_merge_is_atomic_and_returns_exact_preimages(tmp_path):
    adapter, gate = _gate(tmp_path)
    winner, loser, review = _seed(adapter)
    org = Node(id="u:organization:o", type="organization", name="Org")
    affected = Edge(
        id=f"{loser.id}|employed_by|{org.id}", semantic_type="employed_by",
        summary="works", sources=[{"doc": "raw/x.md"}],
    )
    adapter.upsert_nodes([org])
    adapter.upsert_edges([affected])

    audit = gate.review_merge(review.id, winner.id, loser.id)

    assert adapter.get(winner.id).summary == "longer summary"
    assert adapter.get(loser.id).status == "tombstoned"
    assert adapter.conn.execute("SELECT 1 FROM edges WHERE id=?", (review.id,)).fetchone() is None
    assert adapter.conn.execute(
        "SELECT 1 FROM edges WHERE id=?", (f"{winner.id}|employed_by|{org.id}",)
    ).fetchone()
    assert audit.action == "review_confirm"
    assert audit.winner_before == winner.model_dump(mode="json")
    assert audit.loser_before == loser.model_dump(mode="json")
    assert audit.edges_before == [
        review.model_dump(mode="json"), affected.model_dump(mode="json")
    ]


def test_reject_updates_relational_and_serialized_status(tmp_path):
    adapter, gate = _gate(tmp_path)
    winner, loser, review = _seed(adapter)

    audit = gate.reject_review(review.id)

    row = adapter.conn.execute("SELECT status, data FROM edges WHERE id=?", (review.id,)).fetchone()
    assert row["status"] == "rejected"
    assert Edge.model_validate_json(row["data"]).status == "rejected"
    assert adapter.get(winner.id).status == adapter.get(loser.id).status == "active"
    assert audit.action == "review_reject"
    assert audit.edges_before == [review.model_dump(mode="json")]


@pytest.mark.parametrize("operation", ["confirm", "reject"])
def test_non_pending_review_errors_without_mutation(tmp_path, operation):
    adapter, gate = _gate(tmp_path)
    winner, loser, review = _seed(adapter, edge_status="active")
    before = _snapshot(adapter)

    with pytest.raises(ValueError, match="pending"):
        if operation == "confirm":
            gate.review_merge(review.id, winner.id, loser.id)
        else:
            gate.reject_review(review.id)

    assert _snapshot(adapter) == before


@pytest.mark.parametrize("case", ["winner", "type", "self", "tombstoned"])
def test_invalid_review_merge_errors_without_mutation(tmp_path, case):
    adapter, gate = _gate(tmp_path)
    winner, loser, review = _seed(
        adapter,
        loser_type="organization" if case == "type" else "person",
        loser_status="tombstoned" if case == "tombstoned" else "active",
        self_edge=case == "self",
    )
    before = _snapshot(adapter)

    with pytest.raises(ValueError):
        if case == "winner":
            gate.review_merge(review.id, "u:person:not-endpoint", loser.id)
        else:
            gate.review_merge(review.id, winner.id, loser.id)

    assert _snapshot(adapter) == before


def test_review_merge_rolls_back_edge_and_nodes_on_mid_merge_error(tmp_path, monkeypatch):
    adapter, gate = _gate(tmp_path)
    winner, loser, review = _seed(adapter)
    before = _snapshot(adapter)
    original = adapter.upsert_nodes
    calls = 0

    def fail_second(nodes):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError("forced")
        return original(nodes)

    monkeypatch.setattr(adapter, "upsert_nodes", fail_second)
    with pytest.raises(RuntimeError, match="forced"):
        gate.review_merge(review.id, winner.id, loser.id)

    assert _snapshot(adapter) == before


def test_explicit_merge_requires_distinct_active_same_type_nodes(tmp_path):
    adapter, gate = _gate(tmp_path)
    winner, loser, _ = _seed(adapter)
    gate.merge(winner.id, loser.id)
    assert adapter.get(winner.id).status == "active"
    assert adapter.get(loser.id).merged_into == winner.id

    for args in [(winner.id, winner.id), (winner.id, "missing")]:
        with pytest.raises(ValueError):
            gate.merge(*args)
