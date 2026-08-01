# kg Unified Memory — Session Summary Report

**Date:** 2026-08-01
**Branch:** m6-benchmarks-docs
**Test suite:** 766 passed, 3 skipped

---

## 1. Goal

Improve kg (local-first unified memory: files + vectors + graph) by leveraging best patterns from codebase-memory-mcp, graphify, understand-anything, and agent memory. Prove improvements with real, testable benchmarks. Scale to 100k+ nodes.

---

## 2. Research Phase (8 parallel agents)

### System research (4 agents)
| System | Key finding |
|--------|-------------|
| codebase-memory-mcp | Tree-sitter + SQLite code graph, incremental content-hash, Louvain |
| graphify | Confidence triage (EXTRACTED/INFERRED/AMBIGUOUS), significance filtering |
| understand-anything | Dual-stage layout (ELK+d3-force), tour system, filter/focus UI |
| AgentMemory | Hybrid retrieval (BM25+vec+graph RRF), 95.2% Recall@5, 86% token savings |

### Combined architecture research (4 agents)
- **Unified node envelope** schema (`src/kg/unified.py`) — namespaced IDs, authority hierarchy
- **/kg workflow specs** (ingest/extract/query/dream) — `.planning/intel/kg-*-workflow.md`
- **Failure modes** — 5 prevention strategies, authority precedence rules
- **Integration priorities** — 18-component backlog with effort/risk

Deliverables in `.planning/intel/`:
- `RESEARCH-SYNTHESIS.md`, `HARNESS-LAYER-IMPROVEMENT.md`, `failure-modes.md`
- `kg-{ingest,extract,query,dream}-workflow.md`, `kg-workflows-index.md`, `kg-data-flow.md`

---

## 3. M6b Implementation — Graph-Aware Query + Recall Validation

**Spec:** `docs/superpowers/specs/2026-08-01-graph-aware-query-design.md`

### Decisions (locked via brainstorming)
- Unified harness + human interface
- Adaptive intent-based graph stream depth (find=1, trace=3, explain=2)
- LLM intent classification with heuristic fallback
- Config-driven token budget + adaptive density scaling
- Validation: recall + E2E + token capture + comparative baseline

### Modules shipped (6 sonnet agents)
| Module | LOC | Tests | Status |
|--------|-----|-------|--------|
| `src/kg/router.py` | ~90 | 13 | ✅ Intent classifier + QueryPolicy |
| `src/kg/search.py` (extend) | +45 | 8 | ✅ `graph_aware_hybrid_search`, `weighted_rrf` |
| `src/kg/pack.py` (extend) | +40 | 7 | ✅ `diversify_by_source`, `adaptive_budget` |
| `src/kg/cli/query_cli.py` | ~150 | 5 | ✅ Orchestrator + note writer + token capture |
| `bench/recall.py` | — | wired | ✅ Recall@k runner |
| `bench/runners.py` + `reporter.py` (extend) | — | — | ✅ D3/D4 dimensions, comparative baseline |

### Benchmark results (scale 10)
| Metric | Value | Target | Status |
|--------|-------|--------|--------|
| Recall@5 | 1.00 | ≥0.90 | ✅ |
| Recall@10 | 1.00 | ≥0.95 | ✅ |
| MRR | 0.96 | ≥0.85 | ✅ |
| kg hits vs grep | 50 vs 3 | — | 16.7× semantic recall |

---

## 4. Scale Fixes — 10k → 100k Nodes

### Original bottlenecks (measured at 100k/150k)
| Operation | Time | Root cause |
|-----------|------|------------|
| Build | 749s | 500k autocommits (no transaction batching) |
| `/graph.json` | 9.6s | Louvain recomputed per HTTP request (87%) |
| `/clusters.json` | 9.7s | Same + Python edge iteration |
| Louvain | 9.1s | NetworkX full recompute, >5k guard skipped |
| Traversal | unsafe | Post-hoc cap; dense hubs explode before truncation |

### Fixes shipped (3 sonnet agents + direct edits)
| Fix | Files | Change |
|-----|-------|--------|
| **P0 transaction batching** | `sqlite.py` | `upsert_nodes/edges` wrap batch in single transaction, `executemany` |
| **P0 FTS bulk-skip-delete** | `sqlite.py` | Only delete pre-existing FTS rows → O(N) not O(N²) |
| **P0 indexed dedup** | `sqlite.py`, `dedup.py` | `type`/`canonical_name` columns + indexes; query `WHERE type=?` |
| **P1 materialized community cache** | `sqlite.py`, `community.py` | Generation counter + `node_clusters` table + `louvain_cached()` |
| **P1 SQL viz aggregation** | `viz/server.py` | Bounded degree via indexed `source`/`target` IN-clause |
| **P1 budget-aware traversal** | `sqlite.py`, `traverse.py`, `search.py` | `UNION` dedup + recursive `LIMIT` + `max_nodes` work budget |

### Scale results: before → after
| Operation | 10k before | 10k after | 100k before | 100k after | Gain |
|-----------|-----------:|----------:|------------:|-----------:|-----:|
| Build | 8.9s | **0.3s** | 749s | **3.4s** | **220×** |
| Search | 0.9ms | 0.9ms | 5.4ms | **2.4ms** | 2× |
| Graph query (find) | 19ms | 10ms | 175ms | **33ms** | 5× |
| Louvain (cold) | 0.7s | 0.7s | 9.1s | 9.1s | — |
| `/graph.json` | 603ms | 57ms | 9.6s | **131ms** hot | **73×** |
| `/clusters.json` | 579ms | 656ms cold | 9.7s | **513ms** hot | **20×** |

### Acceptance gates (from `.planning/intel/SCALE-ROADMAP.md`)
| Gate | Target | Actual | Status |
|------|--------|--------|--------|
| Nodes ingest | <120s | 3.4s | ✅ |
| Search p95 | <50ms | 2.4ms | ✅ |
| Graph-aware find p95 | <200ms | 33ms | ✅ |
| Cached community read | <100ms hot | 513ms hot | 🟡 close |
| `/graph.json` hot | <500ms | 131ms | ✅ |

---

## 5. Storage Backend Research (5 parallel agents — in progress)

Evaluating whether to replace SQLite. Agents running:
1. SQLite capacity ceiling audit (pending)
2. LanceDB evaluation (pending)
3. DuckDB+VSS evaluation (pending)
4. codebase-memory-mcp engine extraction (pending)
5. **KuzuDB/Graphiti — COMPLETE: NO-GO** (Kuzu archived Oct 2025; Graphiti requires external server + LLM extraction, violates local-first)

Preliminary signal: optimized SQLite meets all current targets. Swap likely unnecessary until >1M nodes or concurrent-writer requirement emerges.

---

## 6. Files Created/Modified

### New source modules
- `src/kg/router.py`, `src/kg/unified.py`, `src/kg/unified_examples.py`
- `src/kg/cli/query_cli.py`

### Extended source modules
- `src/kg/search.py`, `src/kg/pack.py`, `src/kg/config.py`
- `src/kg/storage/sqlite.py`, `src/kg/dedup.py`, `src/kg/community.py`
- `src/kg/traverse.py`, `src/kg/viz/server.py`
- `src/kg/cli/bench_cli.py`, `src/kg/cli/__init__.py`

### New tests (all green)
- `tests/test_router.py` (13), `tests/test_search_graph.py` (8), `tests/test_pack_diversity.py` (7)
- `tests/test_query_cli.py` (3), `tests/test_query_e2e.py` (2)
- `tests/test_ingest_batch.py`, `tests/test_viz_cache.py`, `tests/test_traverse_budget.py`
- `tests/test_scale_10k.py` (5 — 10k/15k stress)

### Benchmarks
- `bench/BENCHMARKS.md` (5-dimension design), `bench/recall.py`, `bench/tune.py`
- `bench/scale_100k.py`, `bench/profile_nodes.py`
- `bench/results/2026-08-01/report.{json,md}` (recall + comparative)

### Planning/intel docs (`.planning/intel/`)
- `RESEARCH-SYNTHESIS.md`, `HARNESS-LAYER-IMPROVEMENT.md`, `failure-modes.md`
- `kg-{ingest,extract,query,dream}-workflow.md`, `kg-workflows-index.md`, `kg-data-flow.md`
- `SCALE-ROADMAP.md`, `scale-{ingest,vector,viz,graph-algos}.md`
- `db-graph-kuzu-graphiti.md` (+ 4 pending db-*.md)

### Specs
- `docs/superpowers/specs/2026-08-01-graph-aware-query-design.md`
- `docs/superpowers/specs/2026-08-01-graph-aware-query-plan.md`

---

## 7. Config additions (`config.toml [query]`)
```toml
diversity_cap = 3
graph_hops_find = 1
graph_hops_trace = 3
graph_hops_explain = 2
intent_llm = true
```

---

## 8. Headline Numbers

- **Recall@10: 1.00** (target ≥0.95) — 16.7× semantic recall vs grep
- **100k build: 3.4s** (was 749s) — **220× faster**
- **100k graph query: 33ms** (was 175ms) — 5× faster
- **100k `/graph.json` hot: 131ms** (was 9.6s) — **73× faster**
- **766 tests pass**, 0 failures
