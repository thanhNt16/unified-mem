# kg Benchmark Design (M6)

**Goal:** Prove kg improves harness performance over time with real, testable metrics.

## Current State (M5)

What exists:
- `corpus.py` — deterministic corpus (10/50/250 docs, seeded RNG, gray-zone pairs)
- `metrics.py` — `graph_cleanliness` (6-dim composite), `speed_metrics` (median/p95)
- `rubric.py` — answer quality (cross_doc_hits, multi_hop_reach, lineage)
- `runners.py` — harness: `run_kg` (gate+search+expand), `run_baseline` (grep)
- `reporter.py` — JSON + markdown output

Gaps for M6:
1. **Indexing speed** — no benchmark for extraction + gate throughput
2. **Visualization load time** — no server render benchmark
3. **Recall@k** — no retrieval accuracy test set
4. **Learning over time** — no pre/post harness session improvement experiment
5. **Time-to-insight** — no user task completion metric

## Benchmark Dimensions

### D1: Indexing Throughput (docs/min)

**Metric:** Wall-clock seconds to extract + gate a corpus.
**Units:** docs/min, median ± p95 across N=5 runs.
**Test:** Extract full corpus (10/50/250 docs) with fake embedder.

**Target:**
- Scale 10: ≥60 docs/min (median)
- Scale 50: ≥50 docs/min (median)
- Scale 250: ≥40 docs/min (median)

**Reference:** agentmemory indexes 500 docs in ~30s (1000 docs/min) with hybrid extraction.

**Implementation:**
```python
# bench/runners.py
def run_indexing_benchmark(corpus_dir, project_dir, *, scale, embedder_opt="fake"):
    from kg.skills.kg_extract import extract_all  # FIXME: needs implementation
    with timed() as t:
        extract_all(corpus_dir, project_dir, embedder=embedder_opt)
    return {"docs": scale, "elapsed_seconds": t.elapsed, "docs_per_min": scale * 60 / t.elapsed}
```

### D2: Visualization Load Time (ms)

**Metric:** Time to serve `/graph.json` with N nodes.
**Units:** milliseconds, median ± p95.
**Test:** `kg viz serve` → curl `localhost:9749/graph.json`, time request.

**Target:**
- 100 nodes: ≤100ms (p95)
- 500 nodes: ≤250ms (p95)
- 2000 nodes (cap): ≤500ms (p95)

**Implementation:**
```python
# bench/runners.py
def run_viz_benchmark(adapter, *, node_count_target):
    from kg.viz.server import _graph_payload
    with timed() as t:
        payload = _graph_payload(adapter)  # truncates at _MAX_NODES
    size_kb = len(json.dumps(payload)) / 1024
    return {"nodes_returned": len(payload["nodes"]), "edges_returned": len(payload["edges"]), "payload_size_kb": size_kb, "elapsed_ms": t.elapsed * 1000}
```

### D3: Recall@k (retrieval accuracy)

**Metric:** Fraction of ground-truth entities retrieved in top-k results.
**Units:** Recall@5, Recall@10, MRR (mean reciprocal rank).
**Test:** Ground-truth entity list → `kg search` → count hits in top-k.

**Target:**
- Recall@5: ≥0.90 (90% of expected entities in top 5)
- Recall@10: ≥0.95
- MRR: ≥0.85

**Reference:** agentmemory: Recall@5 = 95.2%, Recall@10 = 98.6%, MRR = 88.2%.

**Implementation:**
```python
# bench/runners.py
def run_recall_benchmark(adapter, embedder, config, ground_truth_entities):
    hits_at_5 = []
    hits_at_10 = []
    reciprocal_ranks = []
    for entity_name in ground_truth_entities:
        results = hybrid_search(adapter, embedder, entity_name, mode="hybrid", config=config, k=10)
        ranked_names = [adapter.get(node_id).name for node_id, _ in results if adapter.get(node_id)]
        try:
            rank = ranked_names.index(entity_name) + 1
        except ValueError:
            rank = 0
        hits_at_5.append(1 if rank <= 5 else 0)
        hits_at_10.append(1 if rank <= 10 else 0)
        reciprocal_ranks.append(1.0 / rank if rank > 0 else 0.0)
    return {"recall_at_5": sum(hits_at_5) / len(hits_at_5), "recall_at_10": sum(hits_at_10) / len(hits_at_10), "mrr": sum(reciprocal_ranks) / len(reciprocal_ranks)}
```

### D4: Learning Over Time (harness improvement)

**Metric:** Harness task completion time/score pre- vs post- kg memory layer.
**Units:** % reduction in task completion time, % increase in answer quality.
**Test:** User task (find X, trace Y, explain Z) without kg → with kg → compare.

**Target:**
- Time reduction: ≥30% (tasks take 30% less time with kg)
- Quality increase: ≥20% (rubric score 20% higher with kg)
- Token savings: ≥50% (pack_context uses 50% fewer tokens than full context)

**Reference:** agentmemory: 86% token savings, 95%+ recall.

**Implementation:**
```python
# bench/runners.py
def run_learning_experiment(corpus_dir, pre_kg_fn, post_kg_fn):
    """Run same task twice: before kg memory, after kg memory."""
    # Pre-kg: baseline harness (no memory injection)
    with timed() as pre_t:
        pre_result = pre_kg_fn(corpus_dir)
    # Post-kg: harness with kg.pack_context injection
    with timed() as post_t:
        post_result = post_kg_fn(corpus_dir)
    time_reduction = 1.0 - (post_t.elapsed / pre_t.elapsed)
    quality_delta = post_result["rubric"]["score"] - pre_result["rubric"]["score"]
    return {"pre_time_ms": pre_t.elapsed * 1000, "post_time_ms": post_t.elapsed * 1000, "time_reduction_pct": time_reduction * 100, "quality_delta_pct": quality_delta * 100}
```

### D5: Time-to-Insight (user task completion)

**Metric:** Wall-clock time for user to complete realistic task.
**Units:** seconds (median), task success rate.
**Test:** "Find all people who work at Meridian Labs", "Trace Paris person/city disambiguation", "List all docs mentioning cascade AI".

**Target:**
- Task success: ≥90% (9/10 tasks complete correctly)
- Median time: ≤30s per task (with kg)

**Implementation:**
```python
# bench/tasks.py (NEW)
TASKS = [
    {"id": "t1", "query": "people at Meridian Labs", "expected_count": 4, "expected_entities": ["Elena Vasquez", "Marcus Chen", "Aisha Patel", "Sofia Magnusson"]},
    {"id": "t2", "query": "Paris disambiguation", "expected_count": 2, "expected_entities": ["Paris (person)", "Paris (location)"]},
    # ...
]

def run_task_benchmark(adapter, embedder, config, tasks):
    results = []
    for task in tasks:
        with timed() as t:
            hits = hybrid_search(adapter, embedder, task["query"], mode="hybrid", config=config, k=20)
        retrieved = [adapter.get(node_id).name for node_id, _ in hits if adapter.get(node_id)]
        success = set(retrieved) >= set(task["expected_entities"])
        results.append({"task_id": task["id"], "success": success, "elapsed_ms": t.elapsed * 1000, "retrieved_count": len(retrieved)})
    success_rate = sum(r["success"] for r in results) / len(results)
    median_time_ms = statistics.median(r["elapsed_ms"] for r in results)
    return {"success_rate": success_rate, "median_time_ms": median_time_ms, "tasks": results}
```

## Experiment Design: Learning Over Time

### Hypothesis

**H1:** Harness users complete tasks faster and more accurately after kg indexes project memory.

**Variables:**
- Independent: kg memory layer presence (binary: off/on)
- Dependent: task completion time, answer quality (rubric score), token usage
- Control: same user, same task, same corpus

### Procedure

1. **Pre-test** (day 1): User completes N=10 tasks WITHOUT kg memory injection.
2. **Indexing** (day 1-2): `kg raw add` all project docs, `kg-extract` skill builds graph.
3. **Post-test** (day 3): Same user completes same N=10 tasks WITH kg.pack_context injection.

**Metrics per task:**
- Time (seconds) — wall clock from task start to answer submission
- Quality (rubric score) — cross_doc_hits, multi_hop_reach, lineage, composite
- Tokens (input+output) — harness LLM usage

**Analysis:**
- Paired t-test for time/quality differences (pre vs post)
- Effect size (Cohen's d) for practical significance
- Token savings % = (pre_tokens - post_tokens) / pre_tokens

### Success Criteria

- **Primary:** ≥30% time reduction, p < 0.05 (statistically significant)
- **Secondary:** ≥20% quality increase, ≥50% token savings

### Threats to Validity

- **Practice effect:** User learns corpus on pre-test → confounds time reduction.
  - Mitigation: Counterbalance tasks (half users pre→post, half post→pre), or use different but equivalent tasks.
- **Fatigue:** Post-test after full day → slower performance.
  - Mitigation: Schedule tests at same time of day, limit to 30min each.
- **Embedder drift:** Real embedder (bge-small) vs fake (deterministic) changes recall.
  - Mitigation: Run both; report fake-embedder baseline for reproducibility, real-embedder for production signal.

## Test Harness Additions

### New Files

- `bench/tasks.py` — task definitions (TASKS, run_task_benchmark)
- `bench/recall.py` — recall@k test (ground truth entities, run_recall_benchmark)
- `bench/viz.py` — viz load time (run_viz_benchmark)
- `bench/indexing.py` — indexing throughput (run_indexing_benchmark)
- `bench/learning.py` — learning experiment (run_learning_experiment)

### Modified Files

- `bench/runners.py` — add new runner functions
- `bench/reporter.py` — render new metrics (recall, viz, indexing, learning)
- `src/kg/cli/bench_cli.py` — add `--dimension` option (`d1`/`d2`/`d3`/`d4`/`d5`/`all`)

## Execution

```bash
# Run all dimensions
kg bench --scale 50 --dimension all

# Run single dimension
kg bench --scale 10 --dimension d1  # indexing throughput
kg bench --scale 10 --dimension d2  # viz load time
kg bench --scale 10 --dimension d3  # recall@k
kg bench --scale 10 --dimension d4  # learning over time (needs user)
kg bench --scale 10 --dimension d5  # time-to-insight

# CI mode (fast)
kg bench --scale 10 --dimension d1  # <10s
```

## Reporting

`bench/results/{date}/report.md` — table format:

| Dimension | Metric | Target | Actual | Status |
|-----------|--------|--------|--------|--------|
| D1: Indexing | docs/min (scale 50) | ≥50 | TBD | TBD |
| D2: Viz | load ms (2000 nodes) | ≤500 | TBD | TBD |
| D3: Recall | Recall@10 | ≥0.95 | TBD | TBD |
| D4: Learning | time reduction | ≥30% | TBD | TBD |
| D5: Task | success rate | ≥0.90 | TBD | TBD |

## Next Steps

1. Implement `bench/recall.py`, `bench/viz.py`, `bench/indexing.py`, `bench/tasks.py`
2. Wire runners to `kg bench --dimension` CLI
3. Run full suite on scale 10 (dev), 50 (CI), 250 (release)
4. Publish report to `bench/results/{date}/`
5. Update CHANGELOG.md with benchmark results

---

**Deliverable for M6:** Benchmark suite proves kg improves harness performance over time with real metrics: indexing speed, viz load, recall@k, learning, time-to-insight.
