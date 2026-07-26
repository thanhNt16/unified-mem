import pytest

from kg.search import rrf, hybrid_search
from kg.storage.sqlite import SQLiteAdapter
from kg.ontology import Node
from kg.embed import FakeEmbedder
from kg.config import Config


def test_rrf_merges_rank_lists():
    a = ["x", "y", "z"]
    b = ["y", "z", "w"]
    fused = dict(rrf([a, b], k=60))
    top = sorted(fused, key=lambda i: -fused[i])[0]
    assert top == "y"


def test_rrf_empty_lists():
    assert rrf([]) == []
    assert rrf([[], []]) == []


def test_rrf_single_list():
    out = rrf([["a", "b", "c"]], k=60)
    assert [i for i, _ in out] == ["a", "b", "c"]


def test_rrf_scores_decrease_with_rank():
    out = dict(rrf([["a", "b", "c"]], k=60))
    assert out["a"] > out["b"] > out["c"]


def test_hybrid_search_returns_seeds(tmp_path):
    cfg = Config.default()
    ad = SQLiteAdapter(tmp_path / "kg.db")
    emb = FakeEmbedder()
    ad.upsert_nodes([
        Node(id="u:person:d", type="person", name="Demis", summary="DeepMind founder"),
    ])
    hits = hybrid_search(ad, emb, "DeepMind founder", k=5, config=cfg)
    ids = [h[0] for h in hits]
    assert "u:person:d" in ids


def test_hybrid_mode_uses_both_channels(tmp_path):
    cfg = Config.default()
    ad = SQLiteAdapter(tmp_path / "kg.db")
    emb = FakeEmbedder()
    ad.upsert_nodes([
        Node(id="u:person:a", type="person", name="Alice", summary="alpha"),
        Node(id="u:person:b", type="person", name="Bob", summary="beta"),
    ])
    hits = hybrid_search(ad, emb, "alpha", mode="hybrid", k=5, config=cfg)
    assert isinstance(hits, list)
    assert all(isinstance(h, tuple) and len(h) == 2 for h in hits)


def test_bm25_mode_runs(tmp_path):
    cfg = Config.default()
    ad = SQLiteAdapter(tmp_path / "kg.db")
    emb = FakeEmbedder()
    ad.upsert_nodes([
        Node(id="u:person:a", type="person", name="Alice", summary="alpha beta"),
    ])
    hits = hybrid_search(ad, emb, "alpha", mode="bm25", k=5, config=cfg)
    ids = [h[0] for h in hits]
    assert "u:person:a" in ids


def test_semantic_mode_runs(tmp_path):
    cfg = Config.default()
    ad = SQLiteAdapter(tmp_path / "kg.db")
    emb = FakeEmbedder()
    ad.upsert_nodes([
        Node(id="u:person:a", type="person", name="Alice", summary="alpha beta"),
    ])
    hits = hybrid_search(ad, emb, "alpha", mode="semantic", k=5, config=cfg)
    assert isinstance(hits, list)


def test_keyword_mode_accepted(tmp_path):
    cfg = Config.default()
    ad = SQLiteAdapter(tmp_path / "kg.db")
    emb = FakeEmbedder()
    ad.upsert_nodes([
        Node(id="u:person:a", type="person", name="Alice", summary="alpha beta"),
    ])
    for mode in ("hybrid", "bm25", "semantic", "keyword"):
        hits = hybrid_search(ad, emb, "alpha", mode=mode, k=5, config=cfg)
        assert isinstance(hits, list)


def test_invalid_mode_raises(tmp_path):
    cfg = Config.default()
    ad = SQLiteAdapter(tmp_path / "kg.db")
    emb = FakeEmbedder()
    with pytest.raises(ValueError):
        hybrid_search(ad, emb, "x", mode="nope", k=5, config=cfg)


def test_rrf_k_from_config(tmp_path):
    cfg = Config.default()
    cfg.query.rrf_k = 1
    ad = SQLiteAdapter(tmp_path / "kg.db")
    emb = FakeEmbedder()
    ad.upsert_nodes([
        Node(id="u:person:a", type="person", name="Alice", summary="alpha"),
    ])
    hits = hybrid_search(ad, emb, "alpha", mode="hybrid", k=5, config=cfg)
    assert isinstance(hits, list)
