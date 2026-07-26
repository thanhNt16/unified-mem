---
name: kg-query
description: Query the local knowledge graph using hybrid search, NL-Cypher, or deep-search modes. Answers carry source lineage.
---

# kg-query

Answer questions against the local knowledge graph. The graph lives in `kg.db`; raw sources stay cold. Every answer cites lineage via `sources` on each node.

## When to run

- The user asks a question about previously ingested/extracted knowledge.
- The user says "what do we know about X", "find decisions about Y", "who worked on Z".

## Mode selection

See `references/query-modes.md` for full details. Short form:

| Mode | When | Commands used |
|---|---|---|
| **hybrid** (default) | Most questions. Short, factual, lookup-style. | `kg search` then `kg expand` then `kg pack` |
| **NL-Cypher** | Structural queries: "all orgs founded before 2020", "path from X to Y". | `kg cypher <generated-Cypher>` |
| **deep-search** | Exploratory / broad / 50+ potential hits. | `kg wiki build --from-query --hops 3` then read wiki pages |

Default to hybrid. Switch to NL-Cypher only when the question is explicitly structural (path, aggregation, pattern). Switch to deep-search when hybrid returns >50 hits or the user says "everything about", "comprehensive review", "all context on".

## Hybrid mode (default)

This is the 90% path.

1. **Seed search:**
   ```
   kg search "<user's question or key terms>"
   ```
   This runs FTS5/BM25 in parallel with sqlite-vec ANN, fuses via RRF k=60, returns top-10 seed nodes with scores.

2. **Expand** (context around seeds):
   ```
   kg expand <seed_id> --hops 2 --json
   ```
   Bidirectional BFS via recursive CTE. Hard cap: 300 nodes. If cap is hit, keep highest-scoring frontier and log truncation. Repeat for each seed if needed.

3. **Pack** (rank + trim to token budget):
   ```
   kg expand <seed_id> --hops 2 --json | kg pack -b 4000
   ```
   Ranking: `0.5 * RRF + 0.3 * degree-centrality + 0.2 * recency`. Dedupes, trims to budget. Output is markdown.

4. **Answer** using the packed context. Every claim must cite a node's `sources` field.

## NL-Cypher mode

For structural questions the hybrid index can't handle (aggregations, path queries, type-constrained traversals):

1. Read `ontology.json` to know the available node/edge types and their properties.
2. Generate a read-only Cypher query. Only these clauses are allowed: `MATCH`, `WHERE`, `RETURN`, `OPTIONAL MATCH`, `WITH`, `ORDER BY`, `LIMIT`. Any `CREATE`/`MERGE`/`SET`/`DELETE`/`CALL` is rejected by the engine.
3. Run:
   ```
   kg cypher "MATCH (p:person)-[:employed_by]->(o:organization) RETURN p.name, o.name"
   ```
4. Use the results directly (no expand/pack needed for structured output).

ponytail: NL-to-Cypher generation is model-side in M1. `kg cypher` validates the AST server-side and rejects any non-read clause. Keep Cypher simple and read-only by construction.

## Deep-search mode

For exploratory questions needing broad coverage:

1. `kg wiki build --from-query "<question>" --hops 3`
   Materializes `wiki/deep/<slug>/` with:
   - `index.md` — summary index of all entities found
   - `entities/<slug>.md` — per-entity pages with cross-links
   - Cache keyed by query slug + graph version; stale on version bump.
2. Read the index first. Progressive disclosure: index → entity page → raw (last resort).
3. Query never touches `raw/` by default. Only deep-search reads raw as explicit last-resort.

## What this skill does NOT do

- **Write the graph.** Queries are read-only. Use `/kg:extract` for writes.
- **Merge.** Use `/kg:dream`.
- **Ingest.** Use `/kg:ingest`.
- **Touch raw files** (except deep-search last-resort).

## References

- `references/query-modes.md` — detailed mode selection guide and examples.
- Spec §7 (read path) in `docs/superpowers/specs/2026-07-26-kg-unified-memory-design.md`.
- Ontology contract: `src/kg/ontology.py`.
