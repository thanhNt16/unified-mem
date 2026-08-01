# Implementation Plan: Graph-Aware Query + Recall Validation

**Spec:** `docs/superpowers/specs/2026-08-01-graph-aware-query-design.md`

## Execution strategy

Parallel agent dispatch by module boundary. Each agent owns independent files, zero overlap. Gate serializes writes to shared DB.

## Tasks (parallelizable — no file conflicts)

### T1: router.py + test_router.py
**Files:** `src/kg/router.py`, `tests/test_router.py` (NEW only)
**Deliverable:**
- `QueryPolicy` dataclass (mode, hops, cap, budget_tokens, weights)
- `INTENT_WEIGHTS` table: find=(0.4,0.4,0.2), trace=(0.2,0.2,0.6), explain=(0.33,0.33,0.34)
- `HOPS_TABLE`: find=1, trace=3, explain=2
- `IntentClassifier` protocol + `LLMIntentClassifier` + `HeuristicIntentClassifier`
- `classify(query, config)` → policy; LLM when `config.query.intent_llm=True` AND driver set, else heuristic
- Tests: heuristic rules, policy defaults, LLM mock, fallback path

### T2: search.py graph stream + test_search_graph.py
**Files:** `src/kg/search.py` (EXTEND), `tests/test_search_graph.py` (NEW)
**Deliverable:**
- `weighted_rrf(streams, k=60)` helper
- `graph_aware_hybrid_search(adapter, embedder, query, policy, *, config=None)`
- Reuse `expand()` from `kg.traverse`
- Edge cases: empty graph, hops=0, empty seeds → fallback
- Tests: weighted_rrf math, empty graph, hops=0 A/B, density behavior

### T3: pack.py diversity+budget + test_pack_diversity.py
**Files:** `src/kg/pack.py` (EXTEND), `tests/test_pack_diversity.py` (NEW)
**Deliverable:**
- `diversify_by_source(ranked, adapter, max_per_source=3)` pure function
- `adaptive_budget(config_budget, adapter)` deterministic
- Wire into `pack_context` as pre-filters (signature unchanged)
- Tests: diversity cap, source grouping, budget scale thresholds (density <1.0 → 1.2x, >3.0 → 0.7x)

### T4: config.py QueryConfig + query_cli.py + test_query_cli.py + test_query_e2e.py
**Files:** `src/kg/config.py` (EXTEND QueryConfig), `src/kg/cli/query_cli.py` (NEW), `tests/test_query_cli.py` (NEW), `tests/test_query_e2e.py` (NEW)
**Deliverable:**
- `QueryConfig` (budget_tokens=2000, diversity_cap=3, graph_hops_*, intent_llm)
- `query_cli(query, budget_tokens=None, mode=None)` Typer command
- Orchestrator: classify → graph_aware_hybrid_search → pack → write note + log + query-log.jsonl
- Tests: note writer, log append, end-to-end with seed corpus

### T5: bench wiring (recall + comparative + token capture)
**Files:** `bench/runners.py` (EXTEND), `bench/reporter.py` (EXTEND), `src/kg/cli/bench_cli.py` (EXTEND)
**Deliverable:**
- `kg bench --dimension d3` runs `bench/recall.py` (already written)
- `kg bench --dimension d4` runs 5 representative queries E2E
- Token capture: `cost_from_log` parses `bench/results/{date}/query-log.jsonl`
- Comparative: kg recall vs `rg` baseline table in report.md
- Reporter renders D3/D4 sections

### T6: register query_cli in CLI entrypoint
**Files:** `src/kg/cli/__init__.py` (EXTEND), `src/kg/__main__.py` if needed
**Deliverable:**
- Wire `query_cli` into main Typer app
- `kg query --help` works

## Dependencies

```
T1 (router) ─┐
T2 (search) ─┼─► T4 (query_cli) ─► T5 (bench wiring) ─► T6 (register)
T3 (pack) ───┘
```

T1, T2, T3 fully parallel. T4 depends on T1-T3 interfaces (can stub). T5-T6 sequential after T4.

## Validation gates (post-merge)

- `make test` green
- `kg bench --scale 50 --dimension d3` → Recall@5≥0.90, Recall@10≥0.95, MRR≥0.85
- `kg query "<nl>"` → note file + log line + stdout markdown
- `bench/results/{date}/report.md` shows kg vs `rg` comparison
