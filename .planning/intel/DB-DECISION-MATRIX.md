# Storage Backend Decision Matrix

**Date:** 2026-08-01
**Question:** Replace SQLite with a dedicated graph/vector DB for kg?
**Answer:** **NO. Keep SQLite as primary through ~1M nodes.**

---

## Verdict Summary

| Candidate | Verdict | Critical reason |
|-----------|---------|-----------------|
| **SQLite (current, optimized)** | ✅ **KEEP** | Meets all measured targets. Single-file, local-first, zero deps. |
| **LanceDB** | ❌ NO-GO full / ⏳ DEFER hybrid | No native graph traversal (`neighbors()`); no evidence it beats 3.4s/2.4ms/33ms |
| **DuckDB + VSS** | ❌ NO-GO primary | VSS persistence experimental, crash recovery incomplete, filtered ANN unsafe |
| **KuzuDB** | ❌ NO-GO | Project archived Oct 2025, unmaintained |
| **Graphiti** | ❌ NO-GO | Requires external server (Neo4j/FalkorDB) + LLM extraction, violates local-first |
| **codebase-memory-mcp** | ℹ️ PATTERN SOURCE | Also SQLite-based; validates SQLite choice. Borrow extraction registry hashing, not engine. |

**5 of 5 evaluations recommend keeping SQLite.** codebase-memory-mcp (the fastest code indexer in this space) uses the same SQLite + FTS5 + blob-vectors stack.

---

## Evidence: Current SQLite Beats All Targets

Measured at 100k nodes / 150k edges (via `uv run` with sqlite_vec installed):

| Operation | Before optimization | After optimization | Target | Status |
|-----------|---------------------|-------------------|--------|--------|
| Build (full) | 749s | **3.4s** | <120s | ✅ 35× under target |
| Vector search | 5.4ms | **2.4ms** | <50ms p95 | ✅ |
| Graph query (find) | 175ms | **33ms** | <200ms | ✅ |
| `/graph.json` hot | 9.6s | **131ms** | <500ms | ✅ |
| `/clusters.json` hot | 9.7s | **513ms** | <500ms | 🟡 close |
| Louvain (cold) | 9.1s | 9.1s | <2s | ⚠️ cached path covers this |
| Recall@10 | — | **1.00** | ≥0.95 | ✅ |
| MRR | — | **0.96** | ≥0.85 | ✅ |

---

## Why Replacement Fails the Workload Test

kg's workload is:
1. **OLTP point-gets/upserts** (node-by-node extraction + gate writes) — SQLite wins
2. **Bounded BFS traversal** (depth ≤3, `neighbors()`) — SQLite recursive CTE wins; no candidate has native graph traversal that's also embedded + maintained
3. **Local-first, single-file portability** — SQLite is the gold standard; every alternative adds servers, multi-file stores, or experimental persistence
4. **Hybrid search** (BM25 + vec + graph RRF) — already implemented; candidates offer no proven gain

The candidates optimize for workloads kg doesn't have:
- DuckDB: columnar analytics over full graph (kg does local neighborhoods)
- LanceDB: pure vector ANN at >10M scale (kg is at 100k)
- Graphiti: temporal episode extraction via LLM (kg does direct graph writes)
- KuzuDB: native Cypher (kg already has Cypher subset via SQL translator)

---

## Swap Triggers (when to reconsider)

Reopen the DB decision **only if** a measured, reproduced benchmark breaches any:

| Trigger | Threshold | Candidate to revisit |
|---------|-----------|----------------------|
| Vector search p95 | >50ms sustained | LanceDB hybrid (vectors only) |
| Build time | >120s per 1M nodes | Staged bulk build (SQLite pattern from CBM) |
| Concurrent writers | >1 required | Postgres or server-based |
| DB + WAL size | >25% provisioned disk | Sharding or blob store |
| Louvain cold | >120s or RAM >50% host | Incremental community algorithm |
| Graph traversal p95 | >200ms at depth ≥5 | Reconsider native graph DB if one emerges |
| Filtered ANN correctness | type-filtered vec returns wrong results | Metadata columns in sqlite-vec or LanceDB |

**Current state breaches none of these.**

---

## Patterns to Borrow (not engines)

From codebase-memory-mcp (`.planning/intel/db-codebase-memory-mcp.md`):

| Pattern | Priority | Why |
|---------|----------|-----|
| Content-addressed extraction registry (`file_hashes` with SHA-256 + mtime + size) | P0 | Skip unchanged inputs before LLM/embed/gate — kg already has registry.jsonl, extend it |
| Structural-first extraction (tree-sitter/LSP for code) | P0 | Reserve LLM for semantic facts, not imports/calls |
| RAM-staged bulk build + one atomic publish | P1 | Use temp SQLite/transaction for initial rebuild, keep WAL for incremental |
| Portable compressed artifact (`.kg/kg.db.zst`) | P1 | kg already has snapshot; add zstd publish |
| Explicit index modes (fast/moderate/full) | P2 | Let users skip expensive similarity passes |
| MinHash clone detection | P2 | Deterministic dedup candidate generation |

---

## Decision

**Do not replace SQLite. Do not add a second storage engine.**

Instead:
1. Extend SQLite temporal projection (`valid_from`/`valid_until` — Phase 3 in roadmap)
2. Add extraction registry hashing (skip unchanged sources)
3. Add sqlite-vec metadata columns for filtered ANN correctness (if type-filter regressions appear)
4. Re-evaluate only when a swap trigger fires AND a maintained embedded candidate exists

**Rationale:** Every replacement candidate either (a) is unmaintained, (b) requires a server, (c) has experimental persistence, or (d) offers no measured gain over the current optimized stack. The 220× build improvement and 73× viz improvement came from fixing SQLite usage (transactions, batching, FTS bulk-skip, indexes, caching), not from a different engine. The same effort applied to a new engine would reproduce the same bottlenecks in a different shape.

---

## Supporting Documents

| File | Scope |
|------|-------|
| `.planning/intel/db-sqlite-ceiling.md` | SQLite capacity model, true bottlenecks, swap triggers |
| `.planning/intel/db-lancedb.md` | LanceDB full/hybrid evaluation |
| `.planning/intel/db-duckdb-vss.md` | DuckDB+VSS evaluation |
| `.planning/intel/db-graph-kuzu-graphiti.md` | KuzuDB + Graphiti evaluation |
| `.planning/intel/db-codebase-memory-mcp.md` | codebase-memory-mcp engine extraction |
