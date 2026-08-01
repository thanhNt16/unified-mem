# Graph-Aware Query + Recall Validation Design

**Date:** 2026-08-01
**Phase:** M6b (2-week proof)
**Status:** Approved (auto-tuned per goal directive)

## Goal

Prove kg improves harness retrieval with real metrics: extend `hybrid_search` with a graph-neighbor stream, build an intent-aware `/kg:query` router, and validate Recall@k ≥0.95 against a grep baseline.

## Decisions (locked)

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Use case | Unified harness + human | One surface, two transports |
| Graph stream | Adaptive intent-based depth | find=1hop, trace=3, explain=2 |
| Intent detection | LLM classification (fallback to heuristic) | Accuracy over cost; CLI works offline |
| Token budget | Config ceiling + adaptive density | B+C combined |
| Validation | Recall + E2E + tokens + comparative | Full "prove it helps" story |
| Architecture | Layered components (Approach 2) | Isolation, testability |

## Architecture

Six units, each one job:

```
kg query "<nl>"
  → query_cli.py (orchestrator)
    → router.py (LLM intent → QueryPolicy)
    → search.py: graph_aware_hybrid_search (3-stream RRF)
    → pack.py: diversify + adaptive budget
    → wiki notes writer (inline)
```

### Components

| Unit | Responsibility | Status |
|------|----------------|--------|
| `src/kg/cli/query_cli.py` | Orchestrator: parse → dispatch → write note | NEW (~60 LOC) |
| `src/kg/router.py` | NL→QueryPolicy via pluggable intent classifier; INTENT_WEIGHTS table | NEW (~90 LOC) |
| `src/kg/search.py` | `graph_aware_hybrid_search` + `weighted_rrf` | EXTEND (+45 LOC) |
| `src/kg/pack.py` | `diversify_by_source` + `adaptive_budget` | EXTEND (+40 LOC) |
| `bench/recall.py` | Recall@k runner | EXISTS, wire to CLI |
| `bench/runners.py` | Token capture + comparative baseline | EXTEND |
| `src/kg/cli/bench_cli.py` | `--dimension d3/d4` flag | EXTEND |

## QueryPolicy

```python
@dataclass(frozen=True)
class QueryPolicy:
    mode: str          # "find" | "trace" | "explain"
    hops: int          # find=1, trace=3, explain=2
    cap: int           # max nodes fetched
    budget_tokens: int # effective budget
    weights: dict      # {lexical, semantic, graph} RRF weights
```

### Intent classifier (pluggable)

`router.py` exposes an `IntentClassifier` protocol with two implementations:

1. **`LLMIntentClassifier`** (default when harness LLM available): calls harness LLM with a 3-shot prompt, returns intent string. Uses existing `kg` driver config. Adds ~150ms latency per query.
2. **`HeuristicIntentClassifier`** (fallback, zero-dependency): regex/keyword rules — `what calls|who depends on|trace` → trace; `explain|how does|why` → explain; else find. Deterministic, offline-safe.

`router.classify(query)` picks LLM if `config.query.intent_llm=True` AND driver configured; else heuristic. Config key `intent_llm` defaults `True` but degrades silently to heuristic when no LLM driver is set — never errors in CLI-only mode.

### Intent → policy defaults

| Intent | hops | cap | weights (lex/sem/graph) |
|--------|------|-----|------------------------|
| find | 1 | 20 | 0.4 / 0.4 / 0.2 |
| trace | 3 | 50 | 0.2 / 0.2 / 0.6 |
| explain | 2 | 40 | 0.33 / 0.33 / 0.34 |

## Graph-Aware Search

```python
def graph_aware_hybrid_search(adapter, embedder, query, policy, *, config=None):
    bm25_ids = [id for id, _ in adapter.fts_search(query, k=policy.cap * 3)]
    vec_ids  = [id for id, _ in adapter.vec_search(embedder.embed(query), k=policy.cap * 3)]
    seeds = bm25_ids[:10] + vec_ids[:10]
    graph_ids = [n.id for n in expand(adapter, seeds, hops=policy.hops, cap=policy.cap).nodes]
    streams = [
        (bm25_ids,   policy.weights["lexical"]),
        (vec_ids,    policy.weights["semantic"]),
        (graph_ids,  policy.weights["graph"]),
    ]
    return weighted_rrf(streams)[:policy.cap]
```

`weighted_rrf`: per-stream weight × `1/(k + rank + 1)`, k=60.

### Edge cases
- Empty graph → graph stream empty, RRF degrades to 2-stream hybrid
- Seeds hit 0 → fallback to text search, log warning
- `policy.hops=0` → graph stream disabled (A/B baseline)

## Pack Diversity + Budget

```python
def diversify_by_source(ranked, adapter, *, max_per_source=3):
    # group by node.sources[0]["doc"], cap at max_per_source

def adaptive_budget(config_budget, adapter):
    density = active_edges / active_nodes
    scale = 1.2 if density < 1.0 else (0.7 if density > 3.0 else 1.0)
    return min(config_budget, int(config_budget * scale))
```

`pack_context` applies both as pre-filters; signature unchanged.

## Note Writer (inline in query_cli)

Writes `wiki/notes/<YYYYMMDD-HHMMSS>-<slug>.md` with frontmatter (query, intent, budget, ts) + packed markdown. Appends `wiki/log.md` line.

Token capture: `{query, policy, tokens_used, hits}` → `bench/results/{date}/query-log.jsonl`.

## Config additions (`config.toml [query]`)

```toml
[query]
budget_tokens = 2000
diversity_cap = 3
graph_hops_find = 1
graph_hops_trace = 3
graph_hops_explain = 2
```

## Validation Gates

| Gate | Target | Tool |
|------|--------|------|
| Recall@5 | ≥0.90 | `bench/recall.py` scale 50 |
| Recall@10 | ≥0.95 | same |
| MRR | ≥0.85 | same |
| Query latency p95 | ≤50ms | `bench/runners.py` |
| Token usage captured | non-null | `cost_from_log` parses query-log.jsonl |
| `/kg:query` E2E | note + log + stdout | `tests/test_query_e2e.py` |
| Comparative | kg recall vs `rg` | bench report table |

## Testing

- `tests/test_router.py` — intent classify (mock LLM), policy defaults
- `tests/test_search_graph.py` — graph_aware_hybrid_search, weighted_rrf, edge cases
- `tests/test_pack_diversity.py` — diversify_by_source, adaptive_budget (deterministic)
- `tests/test_query_cli.py` — note writer, log append
- `tests/test_query_e2e.py` — seed corpus → kg query → assert artifacts

## Out of scope (deferred)

- AgentMemory bridge (P1.5)
- LLM Wiki materializer cache (P1.6)
- ELK+d3-force viz (P2.9)
- Temporal projection (P3.14)
- Graphify confidence triage (P2.12)

## Tuning values (evidence-based)

- RRF k=60 (agentmemory default)
- diversity_cap=3 (agentmemory default)
- budget default 2000 tokens (agentmemory default)
- hops: find=1, trace=3, explain=2

## Files touched

**NEW:** `src/kg/router.py`, `src/kg/cli/query_cli.py`, `tests/test_router.py`, `tests/test_search_graph.py`, `tests/test_pack_diversity.py`, `tests/test_query_cli.py`, `tests/test_query_e2e.py`

**EXTEND:** `src/kg/search.py`, `src/kg/pack.py`, `src/kg/cli/bench_cli.py`, `bench/runners.py`, `bench/reporter.py`, `src/kg/config.py` (QueryConfig)

**CONFIG:** `.kg/config.toml` template gains `[query]` section
