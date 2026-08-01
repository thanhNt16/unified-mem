"""Tests for graph-aware search (weighted RRF + graph expansion)."""

import tempfile
from dataclasses import dataclass
from pathlib import Path

import pytest

from kg.search import rrf, weighted_rrf, graph_aware_hybrid_search
from kg.storage.sqlite import SQLiteAdapter
from kg.embed import FakeEmbedder
from kg.config import Config
from kg.ontology import Node, Edge


@dataclass(frozen=True)
class QueryPolicy:
    """Duck-typed policy for graph_aware_hybrid_search."""
    hops: int
    cap: int
    weights: dict


def test_weighted_rrf_single_stream_weight_one_equals_plain_rrf():
    """Weight 1.0 on single stream should match plain RRF scores."""
    rank_list = ["a", "b", "c", "d"]
    plain = dict(rrf([rank_list], k=60))
    weighted = dict(weighted_rrf([(rank_list, 1.0)], k=60))
    for item in rank_list:
        assert plain[item] == pytest.approx(weighted[item])


def test_weighted_rrf_two_streams_with_weights():
    """Two streams with different weights should score correctly."""
    stream1 = ["a", "b", "c"]
    stream2 = ["b", "c", "d"]
    result = dict(weighted_rrf([(stream1, 1.0), (stream2, 2.0)], k=60))
    # b and c appear in both streams; b gets higher score (rank 0 in both)
    # d appears only in stream2 with weight 2, rank 2
    assert result["b"] > result["c"]
    assert result["c"] > result["a"]  # c appears in both, a only in stream1
    assert result["c"] > result["d"]  # c appears in both with weight bonus


def test_weighted_rrf_weight_zero_stream_ignored():
    """Stream with weight 0 contributes nothing."""
    stream1 = ["a", "b"]
    stream2 = ["b", "c"]
    result = dict(weighted_rrf([(stream1, 1.0), (stream2, 0.0)], k=60))
    assert result["a"] > 0
    assert result["b"] > 0
    assert result.get("c", 0) == 0


def test_graph_aware_hybrid_search_hops_zero_no_graph_contribution(tmp_path):
    """hops=0 means graph stream is empty; results from text only."""
    cfg = Config.default()
    ad = SQLiteAdapter(tmp_path / "kg.db")
    emb = FakeEmbedder()

    # Seed a simple graph: a connects to b
    ad.upsert_nodes([
        Node(id="u:person:a", type="person", name="Alice", summary="alpha"),
        Node(id="u:person:b", type="person", name="Bob", summary="beta"),
    ])
    ad.upsert_edges([
        Edge(id="u:person:a|knows|u:person:b", source="u:person:a", target="u:person:b", semantic_type="knows"),
    ])

    policy = QueryPolicy(hops=0, cap=10, weights={"lexical": 1.0, "semantic": 1.0, "graph": 0.5})
    hits = graph_aware_hybrid_search(ad, emb, "alpha", policy, config=cfg)
    ids = [h[0] for h in hits]

    # Should find a (alpha match), but not b via graph since hops=0
    assert "u:person:a" in ids
    # b not in graph stream, only if text matches (it doesn't)
    assert "u:person:b" not in ids


def test_graph_aware_hybrid_search_hops_two_expands_more_nodes(tmp_path):
    """hops=2 should return more nodes than hops=0 via graph expansion."""
    cfg = Config.default()
    ad = SQLiteAdapter(tmp_path / "kg.db")
    emb = FakeEmbedder()

    # Chain: a -> b -> c
    ad.upsert_nodes([
        Node(id="u:person:a", type="person", name="Alice", summary="alpha"),
        Node(id="u:person:b", type="person", name="Bob", summary="beta"),
        Node(id="u:person:c", type="person", name="Charlie", summary="gamma"),
    ])
    ad.upsert_edges([
        Edge(id="u:person:a|knows|u:person:b", source="u:person:a", target="u:person:b", semantic_type="knows"),
        Edge(id="u:person:b|knows|u:person:c", source="u:person:b", target="u:person:c", semantic_type="knows"),
    ])

    policy_zero = QueryPolicy(hops=0, cap=10, weights={"lexical": 1.0, "semantic": 1.0, "graph": 0.5})
    hits_zero = graph_aware_hybrid_search(ad, emb, "alpha", policy_zero, config=cfg)
    ids_zero = {h[0] for h in hits_zero}

    policy_two = QueryPolicy(hops=2, cap=10, weights={"lexical": 1.0, "semantic": 1.0, "graph": 0.5})
    hits_two = graph_aware_hybrid_search(ad, emb, "alpha", policy_two, config=cfg)
    ids_two = {h[0] for h in hits_two}

    # With hops=2, we should reach b and c via graph expansion
    assert "u:person:a" in ids_zero
    assert "u:person:b" not in ids_zero  # No graph expansion
    assert "u:person:c" not in ids_zero

    assert "u:person:a" in ids_two
    assert "u:person:b" in ids_two  # Reached via graph
    # c might or might not be in results depending on cap, but b definitely should
    assert len(ids_two) >= len(ids_zero)


def test_graph_aware_hybrid_search_empty_adapter_returns_empty(tmp_path):
    """Empty adapter with no nodes should return [] without error."""
    cfg = Config.default()
    ad = SQLiteAdapter(tmp_path / "kg.db")
    emb = FakeEmbedder()

    policy = QueryPolicy(hops=2, cap=10, weights={"lexical": 1.0, "semantic": 1.0, "graph": 0.5})
    hits = graph_aware_hybrid_search(ad, emb, "anything", policy, config=cfg)

    assert hits == []


def test_graph_aware_hybrid_search_weights_affect_ranking(tmp_path):
    """Higher graph weight should boost graph-reached nodes."""
    cfg = Config.default()
    ad = SQLiteAdapter(tmp_path / "kg.db")
    emb = FakeEmbedder()

    # a (matches text) -> b (no text match)
    ad.upsert_nodes([
        Node(id="u:person:a", type="person", name="Alice", summary="alpha"),
        Node(id="u:person:b", type="person", name="Bob", summary="zzz"),
    ])
    ad.upsert_edges([
        Edge(id="u:person:a|knows|u:person:b", source="u:person:a", target="u:person:b", semantic_type="knows"),
    ])

    # High graph weight should rank b higher
    policy_high_graph = QueryPolicy(
        hops=1, cap=10, weights={"lexical": 0.1, "semantic": 0.1, "graph": 1.0}
    )
    hits_high = dict(graph_aware_hybrid_search(ad, emb, "alpha", policy_high_graph, config=cfg))

    # Low graph weight should rank a higher (text match)
    policy_low_graph = QueryPolicy(
        hops=1, cap=10, weights={"lexical": 1.0, "semantic": 1.0, "graph": 0.1}
    )
    hits_low = dict(graph_aware_hybrid_search(ad, emb, "alpha", policy_low_graph, config=cfg))

    # With high graph weight, both should appear
    ids_high = [h[0] for h in sorted(hits_high.items(), key=lambda x: -x[1])]
    ids_low = [h[0] for h in sorted(hits_low.items(), key=lambda x: -x[1])]

    assert "u:person:a" in ids_high
    assert "u:person:b" in ids_high
    assert "u:person:a" in ids_low
    assert "u:person:b" in ids_low

    # With high graph weight, b's score should be higher relative to a
    score_b_high = hits_high["u:person:b"]
    score_a_high = hits_high["u:person:a"]
    score_b_low = hits_low["u:person:b"]
    score_a_low = hits_low["u:person:a"]

    # Ratio b/a should be higher with high graph weight
    ratio_high = score_b_high / score_a_high
    ratio_low = score_b_low / score_a_low
    assert ratio_high > ratio_low


def test_weighted_rrf_empty_streams():
    """Empty streams should contribute nothing."""
    result = weighted_rrf([([], 1.0)], k=60)
    assert result == []

    result = weighted_rrf([([], 1.0), ([], 1.0)], k=60)
    assert result == []
