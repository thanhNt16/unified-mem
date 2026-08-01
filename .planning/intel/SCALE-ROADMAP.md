# Scale Improvement Roadmap — 100k+ Nodes

**Date:** 2026-08-01
**Evidence:** Measured 100k nodes / 150k edges benchmark + 4-agent code/profile research.

## Measured Baseline

| Operation | 10k / 15k | 100k / 150k | Scale factor | Target |
|---|---:|---:|---:|---:|
| Node build | 8.9s | 749s | 84x | <120s |
| Edge build | included | 8.9s | — | <30s |
| Hybrid search | 0.9ms | 5.4ms | 6x | <50ms |
| Graph-aware find | 19.2ms | 175ms | 9x | <200ms |
| Louvain | 0.7s | 9.1s | 13x | <2s hot/cached |
| `/clusters.json` | 579ms | 9.76s | 17x | <500ms hot |
| `/graph.json` | 603ms | 9.64s | 16x | <500ms hot |

## Confirmed Root Causes

### 1. Node ingest — critical

`SQLiteAdapter` uses `isolation_level=None`; `upsert_nodes()` executes five statements per node without opening a transaction. At 100k nodes this produces roughly **500k autocommits**. The gate also sends single-node lists despite the adapter accepting batches.

Secondary: same-type canonical dedup previously scanned/deserialized all same-type nodes. `vec_search(type_filter)` over-fetches the entire vector table then filters in Python.

### 2. Visualization — critical

Both payload endpoints recompute full NetworkX Louvain on every HTTP request. That accounts for ~8.5s/9.7s (87%). `_graph_payload()` then scans all edges for degrees despite returning only 2,000 nodes. `_cluster_payload()` scans all edges in Python for inter-community counts.

### 3. Traversal — correctness/availability risk

`expand()` runs a recursive `UNION ALL` CTE, materializes all reachable paths/nodes/edges, then applies `cap` in Python. The cap controls presentation, not work. Dense hubs at trace depth=3 can explode before truncation. Query flow may expand the graph twice.

### 4. Vector storage — future ceiling

sqlite-vec search remains fast at 100k (5.4ms), so it is not yet the query bottleneck. Ingest suffers from non-batched writes and no embedding cache. A backend swap is premature until those are fixed and measured.

## Prioritized Work

### P0 — Make ingest transactional (highest ROI, lowest risk)

**Changes**
1. Wrap every adapter batch in one explicit transaction; no statement autocommit inside `upsert_nodes/upsert_edges`.
2. Change gate to collect settled nodes and call `upsert_nodes(batch)` at checkpoints, not once per node.
3. Batch normal-table + FTS writes with `executemany`; test vec0 batch support separately.
4. Keep batch sizes configurable, default 1,000 nodes.

**Expected gain:** 10-100x from eliminating 500k commits; 100k build target <120s.

**Experiment:** compare four cases at 10k/100k: current autocommit, transaction-only, transaction+batch, transaction+batch+deferred FTS. Record nodes/s and DB size.

### P0 — Fix indexed dedup candidates

**Changes**
1. Store indexed `type` and `canonical_name` columns (migration/backfill).
2. Query exact candidate directly: `WHERE status='active' AND type=? AND canonical_name=?`.
3. Keep vector top-k candidates; never deserialize all same-type nodes.
4. Fix type-filtered vector search to avoid fetching all vectors; use vector metadata/filter or post-filter a bounded overfetch.

**Expected gain:** removes quadratic Gate path; stable ingest as same-type population grows.

### P1 — Cache/materialize communities

**Changes**
1. Add graph generation counter incremented on node/edge mutations.
2. Store `{generation, node_id, cluster_id}` in materialized `node_clusters` table.
3. Recompute communities asynchronously/on demand when generation changes; reuse prior valid result until ready.
4. Both viz endpoints read materialized clusters, never invoke Louvain synchronously.
5. Add ETag = graph generation; unchanged browser requests return HTTP 304.

**Expected gain:** payload 9.7s → ~1.5s immediately; hot/304 response <50ms.

### P1 — SQL-side viz aggregation

**Changes**
1. Aggregate inter-cluster edges with SQL joins/GROUP BY against `node_clusters`.
2. Materialize/update node degree on edge changes or aggregate only returned IDs using indexed source/target queries.
3. Add cursor pagination to `/graph.json`; stage-1 clusters remain the default 100k view.

**Expected gain:** cached `/clusters.json` and `/graph.json` <500ms at 100k.

### P1 — Budget traversal during recursion

**Changes**
1. Add traversal work budget inside recursive expansion, not after fetch.
2. Deduplicate visited nodes during traversal; avoid cyclic `UNION ALL` path explosion.
3. Enforce frontier and total-row limits per hop.
4. Return metadata: `truncated`, `completed_hops`, `nodes_examined`.
5. Reuse the graph-aware search subgraph during packing; remove duplicate expansion.
6. If budget expires, return lexical/vector results with explicit truncation.

**Expected gain:** bounded latency/RAM on adversarial hubs; trace p95 <200ms at 100k.

### P2 — Embedding cache + batch embedding

**Changes**
1. Cache key = `(model_id, content_hash(full_context_text))`.
2. Persist cache in SQLite/cache file; invalidate only model/version or content changes.
3. Use existing `embed_many()` for batch extraction; no per-node ONNX calls.

**Expected gain:** 2-10x Gate improvement for initial batch; near-zero re-embed cost on unchanged content.

### P3 — Evaluate vector backend only after P0-P2

**Decision gate**
- Keep sqlite-vec if 100k build <120s and search p95 <50ms.
- Add hybrid LanceDB vectors + SQLite graph/FTS if target >200k or build remains >120s.
- Consider FAISS only >10M vectors or sub-ms search is mandatory.

**Why defer:** sqlite-vec already gives 5.4ms search at 100k. Adding a dependency before fixing 500k autocommits treats the wrong layer.

## Implementation Order

```
P0.1 transaction-only benchmark
  → P0.2 batch upserts
  → P0.3 exact indexed dedup
  → rerun 100k baseline

P1.1 generation + materialized communities
  → P1.2 SQL viz aggregates + ETag
  → rerun 100k viz baseline

P1.3 budgeted traversal + reuse subgraph
  → adversarial hub benchmark

P2 embedding cache + embed_many
  → real embedder benchmark

P3 LanceDB decision (only if gates fail)
```

## Acceptance Gates

| Area | Gate at 100k |
|---|---:|
| Nodes ingest | <120s total, <1.2ms/node |
| Edges ingest | <30s |
| Search p95 | <50ms |
| Graph-aware find p95 | <200ms |
| Graph-aware trace | <200ms or explicit budget truncation |
| Cached communities | <2s recompute; hot read <100ms |
| `/clusters.json` hot | <500ms |
| `/graph.json` hot | <500ms |
| ETag unchanged request | <50ms, HTTP 304 |
| Traversal memory | bounded by configured work budget |
| Correctness | full test suite green; scale and adversarial tests green |

## New Benchmarks Needed

1. `bench/profile_ingest.py`: phase timing (nodes table / FTS / vec / serialize / commits).
2. `bench/scale_100k.py`: preserve current 100k baseline.
3. `tests/test_scale_10k.py`: keep 10k CI-slow tier; register `slow` pytest marker.
4. `bench/adversarial_graph.py`: hub + dense component, trace depth=3, enforce work budget.
5. `bench/viz_cache.py`: cold/hot/ETag request timings and generation invalidation.

## Recommendation

Implement **P0 transactional batching + exact indexed dedup first**. It directly addresses the measured 749s build and changes no public API. Rerun 100k before any storage backend migration. Then cache/materialize communities and implement budget-aware traversal. LanceDB remains an evidence-triggered option, not a default dependency.
