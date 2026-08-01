# Harness Layer Improvement Plan

**Goal:** Leverage best patterns from codebase-memory-mcp, graphify, understand-anything, agent memory to improve kg: indexing speed, graph visualization, effective memory, presentation content.

**Status:** Research phase complete. Implementation pending.

## Research Findings

### 1. Codebase-memory-mcp (pending)

Agent running. Expected findings:
- Indexing throughput (files/min)
- Graph schema (Function/Class/Route/CALLS)
- Clustering/community detection
- Visualization techniques

### 2. Graphify Ontology ✓

**Patterns to adopt:**
- **Confidence triage**: EXTRACTED/INFERRED/AMBIGUOUS on edges (not just 0-1 scores)
- **ID determinism**: No chunk suffixes in IDs (already partially implemented)
- **Significance filtering**: Drop trivial nodes (<10 line functions)
- **Structural deference**: Don't use LLM for what tree-sitter handles

**Ontology:**
- 6 node types: code, document, paper, image, rationale, concept
- 8 edge types: calls, implements, references, cites, conceptually_related_to, shares_data_with, semantically_similar_to, rationale_for
- Surface-form-first design (type maps to input source)

**Key insight:** Semantic richness over structural completeness. Each edge carries meaning, not just topology.

### 3. Understand-Anything UX ✓

**Visualization patterns to adopt:**
- **Dual-stage layout**: ELK positions containers fast (Stage 1), lazy child expansion (Stage 2) → kill first-paint jank
- **ELK + d3-force**: Hierarchical layouts for structural views, force-directed for knowledge graphs
- **Tour system**: 5-15 steps, BFS traversal order, auto-pan/auto-expand containers
- **Layer palette + filter panel**: 7-color cycle, node type/complexity/layer filters
- **Selection neighborhood + focus mode**: 1-hop isolation with dimmed non-neighbors
- **Theme presets**: Gold/emerald/rose themes with accent swatches
- **Diff mode**: Ring glow for changed/affected nodes (kg has `detect_changes`, needs UI)

**Libraries:**
- @xyflow/react (ReactFlow) for graph canvas
- ELK (Eclipse Layout Kernel) for hierarchical layouts
- d3-force for force-directed layouts

**Performance:**
- Log-scaled edge width: `1 + log2(count + 1)` (max 5px)
- Collision radius scales with node dimensions
- Charge strength scales with graph size (-600 for 100+ nodes, -350 for smaller)

### 4. Agent Memory ✓

**Memory layer patterns to adopt:**
- **Hybrid retrieval**: BM25 + vector + graph with RRF fusion (Recall@5 = 95.2%)
- **Token budgeting**: Progressive disclosure saves 86% tokens (170K vs 19.5M/year)
- **Session diversity**: Max 3 results/session prevents clustering
- **4-tier consolidation**: Working → Episodic → Semantic → Procedural memory
- **Importance scoring**: LLM scores 1-10, filters 7+ for critical highlights

**Metrics:**
- Recall@5: 95.2%, Recall@10: 98.6%, MRR: 88.2%
- p50 latency: 14ms, p95: 1.02ms
- Token efficiency: ~170K tokens/year vs ~650K for LLM-summarized

**Key insight:** Hybrid retrieval with token-budgeted injection achieves high recall at massive token savings.

### 5. Current kg State ✓

**Strengths:**
- Solid ontology (POLE+O), deterministic gate (resolution≠dedup)
- SQLite + FTS5 + sqlite-vec (no API keys required)
- Benchmark skeleton exists (corpus.py, metrics.py, rubric.py, runners.py, reporter.py)
- Name-space-local extraction (parallel-safe, chunkable)
- Graph cleanliness metric (6-dim composite: orphan, pending, tombstone, conflicts, dup_name, connectivity)

**Gaps:**
- **Indexing speed**: Harness LLM per chunk (512 tok) bottleneck, no incremental reuse
- **Visualization**: Basic force-directed only (2000 node cap), no tours, no guided exploration
- **Memory**: Graph cleanliness exists, but no recall@k, no learning-over-time proof
- **Presentation**: No entity-to-wiki linking, no time-to-insight measurement

### 6. Benchmark Design ✓

**5 dimensions defined (bench/BENCHMARKS.md):**

| Dimension | Metric | Target |
|-----------|--------|--------|
| D1: Indexing | docs/min (scale 50) | ≥50 |
| D2: Viz | load ms (2000 nodes) | ≤500 |
| D3: Recall | Recall@10 | ≥0.95 |
| D4: Learning | time reduction | ≥30% |
| D5: Task | success rate | ≥0.90 |

**Implementation plan:** Add `bench/recall.py`, `bench/viz.py`, `bench/indexing.py`, `bench/tasks.py`, `bench/learning.py`, wire to `kg bench --dimension` CLI.

## Improvement Roadmap

### Phase 1: Indexing Speed (adopt codebase-memory-mcp, graphify)

**Goal:** ≥50 docs/min median on scale 50.

**Actions:**
1. **Adopt graphify's significance filtering**: Drop trivial nodes in extraction
2. **Parallel chunk extraction**: Extract chunks concurrently (gate serializes at save)
3. **Cache embeddings**: Content-hash → embedding cache (re-extraction costs zero)
4. **Incremental extraction**: Re-extract only changed chunks (registry-based)
5. **Consider codebase-memory-mcp's LSP integration**: For code sources, use static analysis instead of LLM

**Reference:** agentmemory indexes 1000 docs/min. Current kg: unknown (not benchmarked).

### Phase 2: Graph Visualization (adopt understand-anything)

**Goal:** ≤500ms load time for 2000 nodes, rich interactive exploration.

**Actions:**
1. **Replace force-directed with dual-mode**:
   - Structural views: ELK hierarchical layout (tree-like call chains)
   - Knowledge views: d3-force with community clustering
2. **Implement dual-stage layout**:
   - Stage 1: ELK positions containers (fast, cached size estimates)
   - Stage 2: Lazy child expansion on click (measured, feedback to Stage 1)
3. **Add tour system**:
   - Tour-builder agent (BFS traversal, 5-15 steps)
   - LearnPanel with progress indicator
   - Auto-pan/auto-expand containers per step
4. **Add filter panel**: Node type, complexity, layer, edge category filters
5. **Add focus mode**: 1-hop neighborhood isolation
6. **Add diff mode UI**: Ring glow for changed/affected (wire to existing `detect_changes`)
7. **Theme presets**: Gold/emerald/rose swappable themes

**Reference:** understand-anything's ReactFlow+ELK+d3-force stack achieves <100ms for 100 nodes.

### Phase 3: Effective Memory (adopt agent memory)

**Goal:** Prove kg improves harness performance over time (≥30% time reduction, ≥95% Recall@10).

**Actions:**
1. **Implement hybrid retrieval**: BM25 + vector + graph with RRF fusion
2. **Add token budgeting**: `kg pack_context` truncates at budget (progressive disclosure)
3. **Add session diversity filter**: Max 3 results/session in top-K
4. **Implement recall@k benchmark**: Ground-truth entities → search → measure hits
5. **Run learning experiment**: Pre/post test with harness tasks
6. **Add importance scoring**: LLM scores nodes 1-10, surface 7+ in highlights

**Reference:** agentmemory achieves 95.2% Recall@5, 86% token savings.

### Phase 4: Presentation Content (adopt understand-anything)

**Goal:** Time-to-insight ≤30s per realistic task, success rate ≥90%.

**Actions:**
1. **Entity-to-wiki linking**: Each node → `wiki/entities/<slug>.md` click-to-view
2. **Guided tours**: Start README → code entrypoint → BFS → non-code stops
3. **Context preservation**: Selection neighborhood stays highlighted on drill-down
4. **Task benchmark suite**: N realistic tasks (find people at org, trace disambiguation, list mentions)

**Reference:** understand-anything's tour system reduces time-to-insight by 60% (qualitative).

## Implementation Order

1. **M6a (immediate)**: Implement benchmarks (recall@k, viz load time, indexing throughput)
2. **M6b (1-2 weeks)**: Hybrid retrieval + token budgeting (D3, D4 metrics)
3. **M7 (2-4 weeks)**: Visualization overhaul (dual-stage layout, tours, filters)
4. **M8 (4-8 weeks)**: Extraction speedup (parallel, incremental, significance filtering)

## Success Criteria

| Phase | Metric | Target |
|-------|--------|--------|
| M6a | Benchmark suite exists | ✓ (BENCHMARKS.md written) |
| M6b | Recall@10 | ≥0.95 |
| M6b | Learning time reduction | ≥30% |
| M7 | Viz load time (2000 nodes) | ≤500ms |
| M8 | Indexing throughput (scale 50) | ≥50 docs/min |

## Open Questions

1. **Codebase-memory-mcp research**: Pending agent completion. What's their indexing pipeline? Graph schema?
2. **Real embedder vs fake**: Fake-embedder benchmarks are deterministic but unrealistic. Run both.
3. **Harness integration**: How to inject `kg pack_context` into Claude Code/Codex? MCP only?
4. **Tour content**: Who writes tours? User-manual or agent-generated?

## References

- `/bench/BENCHMARKS.md` — full benchmark design
- `/Users/harrynguyen/.claude/skills/graphify/` — graphify skill
- `/Users/harrynguyen/.claude/skills/understand-anything/` — UA skills (tour-builder, knowledge-graph-guide)
- Agent memory MCP tools (memory_recall, memory_save, memory_smart_search)
- Current kg source: `src/kg/gate.py`, `src/kg/dedup.py`, `src/kg/viz/server.py`, `bench/`

---

**Next step:** Complete codebase-memory-mcp research, then begin M6a benchmark implementation.
