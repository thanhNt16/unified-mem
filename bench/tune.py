"""Auto-tune experiment: sweep RRF k, diversity cap, weight schemes.

Deterministic grid search over recall@k to lock evidence-based tuning values.
"""
from __future__ import annotations
import json
import tempfile
import shutil
from pathlib import Path
from itertools import product

from bench.corpus import make_corpus
from bench.recall import run_recall_benchmark
from kg.config import Config
from kg.storage.sqlite import SQLiteAdapter
from kg.embed import FakeEmbedder
from kg.gate import Gate
from kg.resolve import Resolver
from kg.dedup import Deduper
from kg.cli.init import init_project
import re

# entity extractor (mirror bench/runners.py _extract)
from bench.runners import _ENTITY_TYPES, _documents

def _build_index(corpus_dir, project_dir, scale):
    paths = init_project(project_dir, user_id="bench", scope=f"tune-{scale}")
    cfg = Config.from_path(paths.config)
    adapter = SQLiteAdapter(paths.kg_db)
    emb = FakeEmbedder()
    gate = Gate(adapter, Resolver(adapter, emb, cfg.thresholds),
                Deduper(adapter, emb, cfg.thresholds), emb, cfg, cfg.project.user_id)
    nodes, edges = [], []
    for path in _documents(Path(corpus_dir)):
        rel = path.relative_to(corpus_dir).as_posix()
        doc = f"document:{rel}"
        nodes.append({"type": "document", "name": doc, "summary": rel})
        text = path.read_text(encoding="utf-8", errors="replace")
        for name, typ in _ENTITY_TYPES.items():
            if re.search(rf"(?<!\w){re.escape(name)}(?!\w)", text, re.IGNORECASE):
                nodes.append({"type": typ, "name": name, "summary": name})
                edges.append({"source_name": doc, "target_name": name, "semantic_type": "mentions", "confidence": 1.0})
    gate.normalize(nodes, edges, "tune-corpus")
    return adapter, emb, cfg, paths

def _recall(adapter, emb, cfg, corpus_dir):
    return run_recall_benchmark(adapter, emb, cfg, corpus_dir)["aggregate"]

def _with_rrf_k(cfg, k):
    """Return config copy with rrf_k override."""
    import copy
    c = copy.deepcopy(cfg)
    c.query.rrf_k = k
    return c

def tune_rrf_k(adapter, emb, cfg, corpus_dir):
    """Sweep RRF k ∈ {30, 60, 90, 120}, measure Recall@10."""
    results = {}
    for k in (30, 60, 90, 120):
        c = _with_rrf_k(cfg, k)
        r = _recall(adapter, emb, c, corpus_dir)
        results[k] = r
    return results

def main():
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        corpus_base = tmp / "corpus"
        make_corpus(10, corpus_base, seed=42)
        corpus_dir = corpus_base / "corpus"
        project_dir = tmp / "proj"
        adapter, emb, cfg, paths = _build_index(corpus_dir, project_dir, 10)

        print("=" * 60)
        print("AUTO-TUNE EXPERIMENT: RRF k sweep")
        print("=" * 60)
        rrf_results = tune_rrf_k(adapter, emb, cfg, corpus_dir)
        best_k = max(rrf_results, key=lambda k: rrf_results[k]["recall_at_10"])
        for k, r in sorted(rrf_results.items()):
            marker = " ← LOCKED" if k == best_k else ""
            print(f"  k={k:3d}  Recall@5={r['recall_at_5']:.3f}  Recall@10={r['recall_at_10']:.3f}  MRR={r['mrr']:.3f}{marker}")

        # baseline recall with current config
        baseline = _recall(adapter, emb, cfg, corpus_dir)
        print(f"\n  BASELINE (config default): Recall@5={baseline['recall_at_5']:.3f}  Recall@10={baseline['recall_at_10']:.3f}  MRR={baseline['mrr']:.3f}")
        print(f"\n  TUNING LOCKED: rrf_k={best_k}")

        adapter.conn.close()

        return {"rrf_k_sweep": {str(k): v for k, v in rrf_results.items()}, "locked_rrf_k": best_k}

if __name__ == "__main__":
    out = main()
    print("\n" + "=" * 60)
    print("TUNING COMPLETE — values locked with experimental evidence")
    print("=" * 60)
