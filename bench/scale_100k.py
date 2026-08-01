"""100k node scale stress — standalone (not pytest, too slow for main suite)."""
from __future__ import annotations
import tempfile, time
from pathlib import Path
from kg.config import Config
from kg.embed import FakeEmbedder
from kg.storage.sqlite import SQLiteAdapter
from kg.cli.init import init_project
from kg.search import hybrid_search, graph_aware_hybrid_search
from kg.traverse import expand
from kg.pack import pack, diversify_by_source, adaptive_budget
from kg.router import classify
from kg.community import louvain
from kg.ontology import Node, Edge

N_NODES = 100_000
N_EDGES = 150_000

def build(tmp: Path):
    paths = init_project(tmp, user_id="scale", scope="scale-100k")
    cfg = Config.from_path(paths.config)
    adapter = SQLiteAdapter(paths.kg_db)
    emb = FakeEmbedder()

    t0 = time.perf_counter()
    # batch insert nodes
    BATCH = 5000
    for start in range(0, N_NODES, BATCH):
        nodes = [Node(id=f"scale:person:p{i:06d}", type="person", name=f"Person {i}",
                      summary=f"Synthetic person {i}", status="active",
                      sources=[{"doc":"scale-corpus","chunk":0}])
                 for i in range(start, min(start+BATCH, N_NODES))]
        adapter.upsert_nodes(nodes)
    t_nodes = time.perf_counter() - t0

    t0 = time.perf_counter()
    stride = max(1, N_NODES // 3)
    seen = set()
    edges = []
    i = 0
    while len(edges) < N_EDGES and i < N_NODES * 10:
        src = f"scale:person:p{i % N_NODES:06d}"
        tgt = f"scale:person:p{((i*stride)+(i//N_NODES)) % N_NODES:06d}"
        if src == tgt:
            tgt = f"scale:person:p{((i+1)%N_NODES):06d}"
        pair = (src, tgt)
        if pair not in seen:
            seen.add(pair)
            edges.append(Edge(id=f"{src}|knows|{tgt}", semantic_type="knows", status="active"))
        i += 1
    # batch insert edges
    for start in range(0, len(edges), BATCH):
        adapter.upsert_edges(edges[start:start+BATCH])
    t_edges = time.perf_counter() - t0

    return adapter, emb, cfg, paths, t_nodes, t_edges

def main():
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        print("="*60)
        print(f"100K SCALE TEST — {N_NODES} nodes, {N_EDGES} edges")
        print("="*60)
        adapter, emb, cfg, paths, tn, te = build(tmp)
        n = adapter.conn.execute("SELECT COUNT(*) c FROM nodes WHERE status='active'").fetchone()["c"]
        e = adapter.conn.execute("SELECT COUNT(*) c FROM edges WHERE status='active'").fetchone()["c"]
        print(f"build: nodes {tn:.1f}s ({n}), edges {te:.1f}s ({e})")

        # search
        t0 = time.perf_counter()
        hits = hybrid_search(adapter, emb, "Person 50000", mode="hybrid", k=10, config=cfg)
        print(f"search: {(time.perf_counter()-t0)*1000:.1f}ms, hits={len(hits)}")

        # query (find intent)
        policy = classify("find Person 50000")
        t0 = time.perf_counter()
        ranked = graph_aware_hybrid_search(adapter, emb, "Person 50000", policy, config=cfg)
        ranked = diversify_by_source(ranked, adapter, max_per_source=3)
        budget = adaptive_budget(cfg.query.pack_budget_tokens, adapter)
        seed_ids = [nid for nid,_ in ranked]
        sub = expand(adapter, seed_ids, hops=policy.hops, cap=cfg.query.subgraph_cap) if seed_ids else None
        rrf = {nid:s for nid,s in ranked}
        packed = pack(sub, {nid:s for nid,s in ranked}, rrf_scores=rrf, budget_tokens=budget) if sub else ""
        print(f"query (find): {(time.perf_counter()-t0)*1000:.1f}ms, budget={budget}, packed_len={len(packed)}")

        # louvain
        t0 = time.perf_counter()
        clusters = louvain(adapter)
        print(f"louvain: {time.perf_counter()-t0:.1f}s, clusters={len(set(clusters.values()))}, nodes={len(clusters)}")

        # viz payloads
        from kg.viz.server import _cluster_payload, _graph_payload, _MAX_NODES
        t0 = time.perf_counter()
        coarse = _cluster_payload(adapter)
        print(f"/clusters.json: {(time.perf_counter()-t0)*1000:.1f}ms, clusters={coarse['total_clusters']}")
        t0 = time.perf_counter()
        detail = _graph_payload(adapter)
        print(f"/graph.json: {(time.perf_counter()-t0)*1000:.1f}ms, nodes={len(detail['nodes'])} (cap {_MAX_NODES}), truncated={detail['truncated_nodes']}")

        adapter.conn.close()
        print("="*60)
        print("100K SCALE TEST COMPLETE")

if __name__ == "__main__":
    main()
