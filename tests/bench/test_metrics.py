"""Deterministic tests for bench.metrics (M6 T2).

All tests are pure and deterministic. No LLM, no network, no ONNX.
Graph cleanliness is exercised via direct seeding of SQLiteAdapter.
"""
from __future__ import annotations

import json
import sqlite3
import sys
import time
from pathlib import Path

import pytest

# Skip the entire module if sqlite_vec isn't importable (same gating as M1).
sqlite_vec = pytest.importorskip("sqlite_vec")

from kg.ontology import Node, Edge  # noqa: E402
from kg.storage.sqlite import SQLiteAdapter  # noqa: E402
from bench.metrics import (  # noqa: E402
    graph_cleanliness,
    speed_metrics,
    cost_from_log,
    timed,
)


# --- helpers -----------------------------------------------------------------

def _make_node(
    nid: str,
    *,
    type_: str = "person",
    name: str | None = None,
    status: str = "active",
    sources: list[dict] | None = None,
    attribute_conflicts: list[dict] | None = None,
) -> Node:
    return Node(
        id=nid,
        type=type_,
        name=name or nid.split(":")[-1],
        sources=sources if sources is not None else [{"doc": "doc-a", "chunk": "0"}],
        status=status,
        attribute_conflicts=attribute_conflicts or [],
    )


def _edge(src: str, tgt: str, *, sem: str = "knows", status: str = "active") -> Edge:
    from kg.ids import edge_id
    return Edge(
        id=edge_id(src, sem, tgt),
        semantic_type=sem,
        status=status,
    )


@pytest.fixture
def adapter(tmp_path: Path) -> SQLiteAdapter:
    return SQLiteAdapter(tmp_path / "test.db")


# --- graph_cleanliness ------------------------------------------------------

def test_clean_graph_scores_high(adapter: SQLiteAdapter):
    # 3 active, sourced, connected nodes — should be very clean.
    a = _make_node("u:person:A", name="A")
    b = _make_node("u:person:B", name="B")
    c = _make_node("u:person:C", name="C")
    adapter.upsert_nodes([a, b, c])
    adapter.upsert_edges([
        _edge("u:person:A", "u:person:B"),
        _edge("u:person:B", "u:person:C"),
    ])
    result = graph_cleanliness(adapter)
    assert result["total_nodes"] == 3
    assert result["orphan_nodes"] == 0
    assert result["tombstoned_nodes"] == 0
    assert result["attr_conflict_nodes"] == 0
    assert result["duplicate_name_nodes"] == 0
    assert result["connected_nodes"] == 3
    assert result["score"] >= 0.95
    assert 0.0 <= result["score"] <= 1.0


def test_empty_graph_returns_zero_score(adapter: SQLiteAdapter):
    result = graph_cleanliness(adapter)
    assert result["total_nodes"] == 0
    assert result["score"] == 0.0
    assert result["reason"] == "empty graph"


def test_orphans_lower_score(adapter: SQLiteAdapter):
    # Two graphs: clean vs orphan. Orphan must score strictly lower.
    clean_path = adapter.db_path.parent / "clean.db"
    orphan_path = adapter.db_path.parent / "orphan.db"
    clean = SQLiteAdapter(clean_path)
    orphan = SQLiteAdapter(orphan_path)

    sourced = _make_node("u:person:S", name="S", sources=[{"doc": "d", "chunk": "0"}])
    orphan_node = _make_node("u:person:O", name="O", sources=[])
    clean.upsert_nodes([sourced])
    orphan.upsert_nodes([sourced, orphan_node])

    sc = graph_cleanliness(clean)["score"]
    so = graph_cleanliness(orphan)["score"]
    assert so < sc, f"orphan graph ({so}) should score lower than clean ({sc})"
    assert graph_cleanliness(orphan)["orphan_nodes"] == 1
    assert graph_cleanliness(orphan)["orphan_fraction"] == pytest.approx(0.5)


def test_pending_same_as_lowers_score(adapter: SQLiteAdapter):
    a = _make_node("u:person:A", name="A")
    b = _make_node("u:person:B", name="B")
    adapter.upsert_nodes([a, b])
    adapter.upsert_edges([_edge("u:person:A", "u:person:B", sem="same_as", status="pending")])
    result = graph_cleanliness(adapter)
    assert result["pending_same_as"] == 1
    # 1 pending edge against 2 active nodes -> 0.5 fraction
    assert result["pending_same_as_fraction"] == pytest.approx(0.5)


def test_tombstone_fraction_lowers_score(adapter: SQLiteAdapter):
    a = _make_node("u:person:A", name="A")
    b = _make_node("u:person:B", name="B", status="tombstoned")
    adapter.upsert_nodes([a, b])
    result = graph_cleanliness(adapter)
    assert result["tombstoned_nodes"] == 1
    assert result["total_nodes"] == 2
    assert result["tombstone_fraction"] == pytest.approx(0.5)


def test_attribute_conflicts_counted(adapter: SQLiteAdapter):
    a = _make_node(
        "u:person:A",
        name="A",
        attribute_conflicts=[{"key": "k", "winner": "x", "loser": "y"}],
    )
    b = _make_node("u:person:B", name="B")
    adapter.upsert_nodes([a, b])
    result = graph_cleanliness(adapter)
    assert result["attr_conflict_nodes"] == 1
    assert result["attr_conflict_rate"] == pytest.approx(0.5)


def test_duplicate_name_nodes_detected(adapter: SQLiteAdapter):
    # A node with -N suffix indicates a content-id collision with a distinct entity.
    base = _make_node("u:person:Paris", name="Paris")
    dup = _make_node("u:person:Paris-2", name="Paris")
    adapter.upsert_nodes([base, dup])
    result = graph_cleanliness(adapter)
    assert result["duplicate_name_nodes"] == 1
    assert result["duplicate_name_rate"] == pytest.approx(0.5)


def test_cross_doc_connectivity(adapter: SQLiteAdapter):
    # A and B connected via active edge; C is an isolated active node.
    a = _make_node("u:person:A", name="A")
    b = _make_node("u:person:B", name="B")
    c = _make_node("u:person:C", name="C")
    adapter.upsert_nodes([a, b, c])
    adapter.upsert_edges([_edge("u:person:A", "u:person:B")])
    result = graph_cleanliness(adapter)
    assert result["connected_nodes"] == 2
    assert result["cross_doc_connectivity"] == pytest.approx(2 / 3)


def test_determinism_same_graph_same_result(adapter: SQLiteAdapter):
    a = _make_node("u:person:A", name="A")
    b = _make_node("u:person:B", name="B", status="tombstoned")
    adapter.upsert_nodes([a, b])
    adapter.upsert_edges([_edge("u:person:A", "u:person:B")])
    r1 = graph_cleanliness(adapter)
    r2 = graph_cleanliness(adapter)
    assert r1 == r2


def test_subscores_in_unit_interval(adapter: SQLiteAdapter):
    a = _make_node("u:person:A", name="A", attribute_conflicts=[{"k": "v"}])
    adapter.upsert_nodes([a])
    result = graph_cleanliness(adapter)
    for k in (
        "orphan_subscore",
        "pending_same_as_subscore",
        "tombstone_subscore",
        "attr_conflict_subscore",
        "duplicate_name_subscore",
        "cross_doc_connectivity",
        "score",
    ):
        assert 0.0 <= result[k] <= 1.0, f"{k} out of [0,1]: {result[k]}"


# --- speed_metrics ----------------------------------------------------------

def test_speed_metrics_returns_expected_keys():
    def trivial():
        # Deterministic trivial work.
        sum(range(100))
    result = speed_metrics(trivial, n=5)
    assert result["n"] == 5
    for k in ("min_seconds", "max_seconds", "median_seconds", "p95_seconds"):
        assert k in result
        assert isinstance(result[k], float)
        assert result[k] >= 0.0
    assert len(result["samples_seconds"]) == 5


def test_speed_metrics_median_within_min_max():
    def trivial():
        sum(range(50))
    r = speed_metrics(trivial, n=7)
    assert r["min_seconds"] <= r["median_seconds"] <= r["max_seconds"]


def test_speed_metrics_n_one():
    def trivial():
        return 0
    r = speed_metrics(trivial, n=1)
    assert r["n"] == 1
    assert r["min_seconds"] == r["max_seconds"] == r["median_seconds"] == r["p95_seconds"]


def test_speed_metrics_rejects_zero_n():
    with pytest.raises(ValueError):
        speed_metrics(lambda: None, n=0)


def test_speed_metrics_monotonic_ish_for_deterministic_work():
    # A heavier workload should not be faster than a lighter one on average.
    def light():
        sum(range(10))

    def heavy():
        for _ in range(50):
            sum(range(1000))

    light_r = speed_metrics(light, n=3)
    heavy_r = speed_metrics(heavy, n=3)
    assert heavy_r["median_seconds"] >= light_r["median_seconds"]


def test_timed_context_manager_yields_elapsed():
    with timed() as t:
        time.sleep(0.001)
    assert t.elapsed > 0.0
    # Sanity: at least the sleep duration.
    assert t.elapsed >= 0.0005


# --- cost_from_log ----------------------------------------------------------

def test_cost_from_log_missing_path_returns_none(tmp_path: Path):
    assert cost_from_log(tmp_path / "nonexistent.jsonl") is None


def test_cost_from_log_empty_path_returns_none(tmp_path: Path):
    p = tmp_path / "empty.jsonl"
    p.write_text("")
    assert cost_from_log(p) is None


def test_cost_from_log_no_usage_returns_none(tmp_path: Path):
    p = tmp_path / "no_usage.jsonl"
    p.write_text(
        json.dumps({"type": "assistant", "content": "hello"}) + "\n"
        + json.dumps({"type": "user", "content": "hi"}) + "\n"
    )
    assert cost_from_log(p) is None


def test_cost_from_log_parses_usage_tokens(tmp_path: Path):
    p = tmp_path / "with_usage.jsonl"
    p.write_text(
        json.dumps({
            "type": "assistant",
            "message": {"usage": {"input_tokens": 100, "output_tokens": 50}},
        }) + "\n"
        + json.dumps({
            "type": "assistant",
            "usage": {"input_tokens": 30, "output_tokens": 20},
        }) + "\n"
    )
    result = cost_from_log(p)
    assert result is not None
    assert result["input_tokens"] == 130
    assert result["output_tokens"] == 70
    assert result["total_tokens"] == 200
    assert result["lines_parsed"] == 2
    assert len(result["sources"]) == 1


def test_cost_from_log_accepts_multiple_paths(tmp_path: Path):
    p1 = tmp_path / "a.jsonl"
    p2 = tmp_path / "b.jsonl"
    p1.write_text(json.dumps({"usage": {"input_tokens": 10}}) + "\n")
    p2.write_text(json.dumps({"usage": {"input_tokens": 20}}) + "\n")
    result = cost_from_log([p1, p2])
    assert result is not None
    assert result["input_tokens"] == 30
    assert len(result["sources"]) == 2


def test_cost_from_log_handles_invalid_json_lines(tmp_path: Path):
    p = tmp_path / "mixed.jsonl"
    p.write_text(
        "not json\n"
        + json.dumps({"usage": {"input_tokens": 5, "output_tokens": 5}}) + "\n"
        + json.dumps({"no_usage_here": True}) + "\n"
    )
    result = cost_from_log(p)
    assert result is not None
    assert result["total_tokens"] == 10
    assert result["lines_parsed"] == 1


def test_cost_null_reason_documents_contract():
    from bench.metrics import cost_null_reason
    reason = cost_null_reason()
    assert isinstance(reason, str)
    assert "ClaudeDriver" in reason or "no usage tokens" in reason.lower()


# --- determinism across module reload ---------------------------------------

def test_determinism_under_repeated_calls(adapter: SQLiteAdapter):
    a = _make_node("u:person:A", name="A")
    b = _make_node("u:person:Paris-2", name="Paris", attribute_conflicts=[{"k": "v"}])
    adapter.upsert_nodes([a, b])
    adapter.upsert_edges([_edge("u:person:A", "u:person:Paris-2")])
    baseline = graph_cleanliness(adapter)
    for _ in range(5):
        assert graph_cleanliness(adapter) == baseline
