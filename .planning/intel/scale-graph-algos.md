# Graph algorithms at 100k+ nodes

**Scope:** research/proposal; no runtime changes. Baseline: 100,000 nodes, 150,000 edges, Louvain **9.1 s / 67 clusters**, graph-aware `find` **175 ms**. Targets: Louvain **<2 s**, graph-aware query **<200 ms** at 100k.

## Current behavior

### Community detection

[`src/kg/community.py:20-42`](/Users/harrynguyen/Desktop/unified-mem/src/kg/community.py#L20-L42) fetches every active node and edge into Python, creates `active_ids`, an `edges` list, then creates a full undirected `networkx.Graph`. NetworkX is entirely in-process; SQLite is only the input store. Space is therefore **O(V + E)** Python objects, plus transient copies.

The >5,000 path [`community.py:53-71`](/Users/harrynguyen/Desktop/unified-mem/src/kg/community.py#L53-L71) finds components *after* building the full graph, then invokes `louvain_communities()` once per component. It does not impose a component-size cap: [`G.subgraph(comp_nodes)`](/Users/harrynguyen/Desktop/unified-mem/src/kg/community.py#L66) may be 100k nodes.

**Measured benchmark topology:** [`bench/scale_100k.py:16-50`](/Users/harrynguyen/Desktop/unified-mem/bench/scale_100k.py#L16-L50) deterministically generates one weakly connected component: **1 component, 100,000 nodes, 150,000 edges**. Thus the reported 9.1 s is one full-graph NetworkX Louvain run, not 67 independent components. The 67 clusters are Louvain communities.

Louvain is commonly characterized as roughly O(P·E), where P is optimization passes/levels; node count alone is not predictive. NetworkX implementation cost is materially higher than compiled graph libraries because nodes, adjacency maps, edge tuples, row lists, and strings are CPython objects. At this benchmark's 100k/150k, expect graph construction plus algorithm working state to require **at least hundreds of MiB peak process memory**; exact cost depends on string length, Python version, graph degree distribution, and Louvain passes. Peak also includes `rows`, `node_ids`, `active_ids`, `edges`, NetworkX adjacency dictionaries, and component/subgraph bookkeeping. It is not streaming.

**Ceiling:** 100k sparse nodes is already beyond a reliable sub-2-second NetworkX Louvain envelope. Low-degree disconnected graphs can remain usable into low millions only if RAM permits; a giant component, high average degree, or weak modular structure quickly makes both latency and memory unpredictable. Treat **~100k / 150k** as the current synchronous recompute ceiling, not a growth target.

### Traversal

[`src/kg/traverse.py:40`](/Users/harrynguyen/Desktop/unified-mem/src/kg/traverse.py#L40) delegates expansion to SQLite. [`src/kg/storage/sqlite.py:202-225`](/Users/harrynguyen/Desktop/unified-mem/src/kg/storage/sqlite.py#L202-L225) recursively expands with `UNION ALL`, then fetches *all* distinct reached nodes, deserializes every node one-at-a-time, and fetches every active induced edge. The output cap in [`traverse.py:51-67`](/Users/harrynguyen/Desktop/unified-mem/src/kg/traverse.py#L51-L67) runs **after** all of that work.

Trace policy is three hops and cap 50 ([`router.py:23-25`](/Users/harrynguyen/Desktop/unified-mem/src/kg/router.py#L23-L25)). Graph-aware search supplies up to 20 seeds ([`search.py:75-84`](/Users/harrynguyen/Desktop/unified-mem/src/kg/search.py#L75-L84)). For average undirected degree d, distinct-reach upper bound is approximately:

```
min(V, S × (1 + d + d(d-1) + d(d-1)^2)) ; S <= 20
```

For the benchmark average degree ≈3, this is only about 300 nodes before overlaps. For a hub/dense graph it reaches all 100k nodes. Worse, the CTE uses `UNION ALL`: cyclic graph walks are not deduplicated during recursion. Work is bounded by path count, approximately `S × Σ d^i` through hop 3, not distinct-node count. A dense component is therefore superlinear/cubic in practical SQL work for depth 3 before the Python cap truncates the response.

`cap=300` or trace `cap=50` bounds only returned `Subgraph.nodes`; it **does not bound recursive rows, node deserialization, induced-edge scan, centrality, sorting, or peak memory**. It is effective presentation control, not a traversal safety limit.

### Graph-aware hybrid search

[`src/kg/search.py:68-97`](/Users/harrynguyen/Desktop/unified-mem/src/kg/search.py#L68-L97) limits lexical/vector candidates to `3 × cap`, seeds to 20, then calls `expand()` using `policy.hops`. Consequently trace mode can explode exactly as above. `find` (one hop) produced the measured acceptable 175 ms; this does not validate trace mode. The CLI subsequently calls `expand()` again to pack context ([`bench/scale_100k.py:74-83`](/Users/harrynguyen/Desktop/unified-mem/bench/scale_100k.py#L74-L83)), duplicating traversal cost.

## Ranked improvements

1. **Budget traversal inside SQL — highest urgency.** Carry a visited frontier, enforce a hard row/node budget *during each hop*, stop before fan-out exceeds the budget, return a `truncated` signal. Fetch only bounded IDs and edges. Prefer deterministic seed/frontier order. This protects latency/RAM; output truncation alone does not.
   - Default: trace cap 50, traversal work budget 200-500 nodes/edges, per-hop frontier budget; tune against recall.
   - Auto-decrease hops only as a secondary guard: if seed degree/frontier budget predicts overflow, stop at the last complete hop and report truncation. Do not silently claim a three-hop trace was complete.

2. **Remove duplicated expansion.** Have `graph_aware_hybrid_search()` return/reuse its bounded subgraph, or defer expansion until packing. Current query paths can traverse the same seed set twice. This is a direct latency saving, no algorithm change.

3. **Replace NetworkX full recompute with compiled Leiden; batch updates.** Build `igraph.Graph` from edge rows and run Leiden in C/C++; retain previous membership as `initial_membership` for batch refreshes where stable IDs matter. Recompute on a mutation batch/change-ratio threshold, not every write. Leiden guarantees connected communities; benchmark real topology and use p95 gates. This is the credible route to <2 s at sparse 100k; NetworkX tuning is not.
   - Cheap approximate interim: asynchronous label propagation for preview/fallback. Near-linear-like in sparse practice, but stochastic and lower-quality; never silently replace authoritative cluster output.
   - Dynamic/incremental Leiden variants exist but are research-grade. Start with periodic compiled recomputation plus warm start; avoid committing to a dynamic algorithm before workload benchmarks.

4. **Maintain weak components separately.** SQLite recursive CTE is useful for reachability within one affected component, given existing `idx_edges_source`/`idx_edges_target` ([`sqlite.py:33-37`](/Users/harrynguyen/Desktop/unified-mem/src/kg/storage/sqlite.py#L33-L37)). It is a poor all-graph WCC algorithm: recursive CTE has no durable global visited-set and repeats work. Maintain insertion-only WCC using union-find/component IDs; edge deletion triggers rebuild of the affected component. Use SQL CTE as bounded rebuild fallback, not primary global clustering.

5. **Add topology-aware operation gates.** Record V, E, average/max degree, largest WCC, component count, traversal recursive-row count, elapsed time, peak RSS. Refuse/defer synchronous community recompute beyond an empirically measured `(largest_component_nodes, edges)` ceiling. Node count alone hides dense/hub failure modes.

## Acceptance gates

| Operation | Gate at 100k | Measurement | Failure behavior |
|---|---:|---|---|
| Authoritative communities | <2 s p95 | 100k/realistic edge distribution; separate load and algorithm timings | Batch/defer; retain prior clusters |
| Graph-aware `find` | <200 ms p95 | end-to-end first query, cache cold | return lexical/vector result if graph budget expires |
| Graph-aware `trace` | bounded, explicit truncation | 20 seeds, hops=3, hub/dense adversarial graph | stop at budget; expose `truncated=true` and completed hops |
| Traversal memory | fixed by work budget | max nodes, edges, CTE rows, RSS | no post-hoc-only cap |
| Components | no all-graph recursive CTE | insertion/deletion workload | union-find insert; rebuild affected component on delete |

## Sources

- NetworkX Louvain API/mechanics: <https://networkx.org/documentation/stable/reference/algorithms/generated/networkx.algorithms.community.louvain.louvain_communities.html>
- Faster Louvain implementation/complexity discussion: <https://www.traag.net/wp/wp-content/papercite-data/pdf/traag_faster_2015.pdf>
- Leiden algorithm, connected-community guarantee, empirical performance: <https://doi.org/10.1038/s41598-019-41695-z>
- igraph Leiden API, `initial_membership`: <https://python.igraph.org/en/latest/api/igraph.community.html>
- leidenalg API: <https://leidenalg.readthedocs.io/en/stable/reference.html>
- NetworkX asynchronous label propagation: <https://networkx.org/documentation/stable/reference/algorithms/generated/networkx.algorithms.community.label_propagation.asyn_lpa_communities.html>
- SQLite recursive CTE graph traversal/dedup behavior: <https://sqlite.org/lang_with.html>
- SQLite BFS/recursive-CTE limitations discussion: <https://sqlite.org/forum/forumpost/cd5ff0a1d49dc52a>
