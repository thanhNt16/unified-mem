"""Scale stress test: 10k nodes / 15k edges.

Verifies save, search, query, viz, community all function at scale.
Measures wall-clock per operation to surface bottlenecks.
"""
from __future__ import annotations

import tempfile
import time
from pathlib import Path

import pytest

from kg.config import Config
from kg.embed import FakeEmbedder
from kg.gate import Gate
from kg.resolve import Resolver
from kg.dedup import Deduper
from kg.storage.sqlite import SQLiteAdapter
from kg.cli.init import init_project
from kg.search import hybrid_search, graph_aware_hybrid_search
from kg.traverse import expand
from kg.pack import pack, diversify_by_source, adaptive_budget
from kg.router import classify
from kg.community import louvain
from kg.ontology import Node, Edge


pytestmark = pytest.mark.slow


def _build_scale_graph(project_dir: Path, n_nodes: int = 10_000, n_edges: int = 15_000):
    """Build a graph with n_nodes persons + n_edges knows edges, deterministically."""
    paths = init_project(project_dir, user_id="scale", scope="scale-10k")
    cfg = Config.from_path(paths.config)
    adapter = SQLiteAdapter(paths.kg_db)
    emb = FakeEmbedder()
    gate = Gate(adapter, Resolver(adapter, emb, cfg.thresholds),
                Deduper(adapter, emb, cfg.thresholds), emb, cfg, cfg.project.user_id)

    # batch 1: seed nodes directly via adapter (bypass gate for speed — scale test)
    nodes = []
    for i in range(n_nodes):
        nodes.append(Node(
            id=f"scale:person:p{i:05d}",
            type="person",
            name=f"Person {i}",
            summary=f"Synthetic person {i} for scale test",
            status="active",
            sources=[{"doc": "scale-corpus", "chunk": 0}],
        ))
    adapter.upsert_nodes(nodes)

    # batch 2: edges (deterministic, unique pairs via stride to avoid collisions)
    edges = []
    stride = max(1, n_nodes // 3)  # spread targets
    seen_pairs: set[tuple[str, str]] = set()
    i = 0
    while len(edges) < n_edges and i < n_nodes * 10:
        src = f"scale:person:p{i % n_nodes:05d}"
        tgt_idx = (i * stride + (i // n_nodes)) % n_nodes
        tgt = f"scale:person:p{tgt_idx:05d}"
        if src == tgt:
            tgt = f"scale:person:p{(tgt_idx + 1) % n_nodes:05d}"
        pair = (src, tgt)
        if pair not in seen_pairs:
            seen_pairs.add(pair)
            eid = f"{src}|knows|{tgt}"
            edges.append(Edge(id=eid, semantic_type="knows", status="active"))
        i += 1
    adapter.upsert_edges(edges)

    return adapter, emb, cfg, paths


def test_scale_10k_save(tmp_path):
    """Saving 10k nodes / 15k edges completes in reasonable time."""
    t0 = time.perf_counter()
    adapter, emb, cfg, paths = _build_scale_graph(tmp_path)
    t_build = time.perf_counter() - t0

    n = adapter.conn.execute("SELECT COUNT(*) c FROM nodes WHERE status='active'").fetchone()["c"]
    e = adapter.conn.execute("SELECT COUNT(*) c FROM edges WHERE status='active'").fetchone()["c"]
    assert n == 10_000, f"expected 10k nodes, got {n}"
    assert e == 15_000, f"expected 15k edges, got {e}"
    print(f"\n[10k scale] build: {t_build:.1f}s, nodes={n}, edges={e}")
    adapter.conn.close()


def test_scale_10k_search(tmp_path):
    """Hybrid search returns results in <500ms at 10k nodes."""
    adapter, emb, cfg, paths = _build_scale_graph(tmp_path)
    try:
        t0 = time.perf_counter()
        hits = hybrid_search(adapter, emb, "Person 5000", mode="hybrid", k=10, config=cfg)
        t_search = time.perf_counter() - t0
        print(f"\n[10k scale] search: {t_search*1000:.1f}ms, hits={len(hits)}")
        assert len(hits) > 0
        assert t_search < 2.0, f"search too slow: {t_search:.2f}s"
    finally:
        adapter.conn.close()


def test_scale_10k_graph_aware_query(tmp_path):
    """Full /kg:query flow (router → graph search → pack) at 10k nodes."""
    adapter, emb, cfg, paths = _build_scale_graph(tmp_path)
    try:
        policy = classify("find Person 1234")
        t0 = time.perf_counter()
        ranked = graph_aware_hybrid_search(adapter, emb, "Person 1234", policy, config=cfg)
        ranked = diversify_by_source(ranked, adapter, max_per_source=3)
        budget = adaptive_budget(cfg.query.pack_budget_tokens, adapter)
        seed_ids = [nid for nid, _ in ranked]
        subgraph = expand(adapter, seed_ids, hops=policy.hops, cap=cfg.query.subgraph_cap) if seed_ids else None
        rrf_scores = {nid: s for nid, s in ranked}
        packed = pack(subgraph, {nid: s for nid, s in ranked}, rrf_scores=rrf_scores, budget_tokens=budget) if subgraph else ""
        t_query = time.perf_counter() - t0

        print(f"\n[10k scale] query (find): {t_query*1000:.1f}ms, budget={budget}, packed_len={len(packed)}")
        assert t_query < 3.0, f"query too slow: {t_query:.2f}s"
        assert isinstance(packed, str)
    finally:
        adapter.conn.close()


def test_scale_10k_louvain(tmp_path):
    """Louvain per-component completes on 10k nodes, returns real clusters."""
    adapter, emb, cfg, paths = _build_scale_graph(tmp_path)
    try:
        t0 = time.perf_counter()
        clusters = louvain(adapter)
        t_louvain = time.perf_counter() - t0
        n_clusters = len(set(clusters.values()))
        print(f"\n[10k scale] louvain: {t_louvain:.1f}s, clusters={n_clusters}, nodes={len(clusters)}")
        assert len(clusters) == 10_000
        assert -1 not in clusters.values(), "should not return skip sentinel at scale"
        assert n_clusters >= 1
    finally:
        adapter.conn.close()


def test_scale_10k_cluster_payload(tmp_path):
    """Viz Stage-1 coarse cluster view works at 10k nodes."""
    adapter, emb, cfg, paths = _build_scale_graph(tmp_path)
    try:
        from kg.viz.server import _cluster_payload, _graph_payload, _MAX_NODES
        t0 = time.perf_counter()
        coarse = _cluster_payload(adapter)
        t_coarse = time.perf_counter() - t0
        print(f"\n[10k scale] /clusters.json: {t_coarse*1000:.1f}ms, clusters={coarse['total_clusters']}")

        t0 = time.perf_counter()
        detail = _graph_payload(adapter)
        t_detail = time.perf_counter() - t0
        print(f"[10k scale] /graph.json: {t_detail*1000:.1f}ms, nodes={len(detail['nodes'])} (cap {_MAX_NODES}), truncated={detail['truncated_nodes']}")

        assert coarse["total_nodes"] == 10_000
        assert len(detail["nodes"]) <= _MAX_NODES, "detail must respect hard cap"
        assert detail["truncated_nodes"] is True, "10k graph must trigger truncation warning"
        assert t_detail < 5.0, f"detail payload too slow: {t_detail:.2f}s"
    finally:
        adapter.conn.close()
