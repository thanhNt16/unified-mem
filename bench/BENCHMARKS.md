# kg Benchmark Guide

How to reproduce kg's performance and quality numbers, and what each dimension
measures. Commands use a deterministic fake-embedder so runs are reproducible
without network access.

## Quick start

```bash
# Full Python test suite (798 collected: 795 passed, 3 skipped)
uv run pytest

# CBM comparative evaluation (kg vs codebase-memory-mcp pattern baseline)
make evaluate-cbm

# Scale-10 benchmark into a temp directory
PYTHONPATH="$PWD" uv run kg bench --scale 10 --out-dir "$(mktemp -d)"

# 100k stress profile (slow)
PYTHONPATH="$PWD" uv run python bench/scale_100k.py
```

CI runs the scale-10 suite on every push to `main` and weekly (`.github/workflows/bench.yml`).

## Modules

| File | Purpose |
|---|---|
| `bench/corpus.py` | Deterministic corpus generator (scale 10/50/250), seeded RNG, gray-zone pairs |
| `bench/metrics.py` | Composite quality + speed metrics (median, p95) |
| `bench/rubric.py` | Answer-quality scoring (cross-doc hits, multi-hop reach, lineage) |
| `bench/runners.py` | Harness runners: `run_kg`, `run_baseline`, recall + comparative dimensions |
| `bench/recall.py` | Recall@k / MRR runner against ground-truth entities |
| `bench/reporter.py` | JSON + markdown report rendering |
| `bench/scale_100k.py` | 100k-node/150k-edge stress profile |
| `bench/evaluate_cbm.py` | Comparative evaluation against codebase-memory-mcp patterns |
| `bench/export_demo_graph.py` | Exports real repo graph to `ui/graph-ui/snapshots/demo-source.json` |
| `bench/gen_stress_graph.py` | Generates deterministic 10k-node / 15k-edge stress dataset to `ui/graph-ui/snapshots/stress-source.json` |

## Dimensions

### D1 — Indexing throughput
Wall-clock time to ingest a corpus. Deterministic fake-embedder; `kg bench
--scale {10,50,250}`. Current 100k build: **3.4s** (target <120s).

### D2 — Graph response latency
Time for `/graph.json` and `/clusters.json` with the materialized community
cache. Current 100k hot: **131ms** `/graph.json` (target <500ms), **513ms**
`/clusters.json` (close).

### D3 — Recall@k / MRR
Fraction of ground-truth entities retrieved in the top-k results, and mean
reciprocal rank, over the deterministic corpus. Current: **Recall@5/10 = 1.00**,
**MRR 0.96** (targets ≥0.90 / ≥0.95 / ≥0.85).

### D4 — Comparative baseline (kg vs grep)
kg-hybrid hits vs grep-only hits on the same corpus. Current: **50 vs 3** (16.7×
semantic recall).

D4 "learning over time" (pre/post harness session, paired-t) remains the most
rigorous validity test; the deterministic corpus gives a reproducible proxy and
a real-embedder run is the production signal. Threats (practice effect,
embedder drift) are mitigated by the fake-embedder baseline.

## Report schema

`--out-dir <dir>` writes `report.json` (machine) and `report.md` (human) with
per-dimension `{metric, target, actual, status, scale}`. **Canonical copies live
in `docs/benchmarks/<YYYY-MM-DD>/`** when results are cited in documentation;
`bench/results/` is the local working directory and is `.gitignore`d except for
the M6 baseline `bench/results/2026-07-27/report.json` (historical reference).

## Limitations (honest)

- Numbers are from a deterministic fake-embedder on small corpora; production
  recall depends on the real embedder (e.g. bge-small). Run both when it matters.
- p95 over N=5 runs — small sample; treat as order-of-magnitude, not significance.
- The pre/post harness "learning" experiment needs a human or scripted harness
  session; the deterministic proxy does not capture practice effects. Paired
  counterbalancing is required for a publishable result.
- Louvain cold path (9.1s @ 100k) is not benchmarked here — it is covered by the
  materialized hot cache, not improved.

## Recent results

Canonical current report: `docs/reports/2026-08-01-status.md` ("Measured
results"). Headline: 749s→3.4s build, 9.6s→131ms `/graph.json` hot, Recall@10
1.00.
