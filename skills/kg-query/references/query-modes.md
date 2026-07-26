# Query Modes Reference

Three modes for reading the knowledge graph. Selection is automatic based on the question shape; the user can override.

---

## 1. Hybrid (default)

**For:** factual lookups, "who/what/when" questions, anything under ~50 potential hits.

**Pipeline:**

```
kg search "<query>"          # FTS5 ∥ ANN, RRF k=60, top-10 seeds
kg expand <id> --hops 2 --json | kg pack -b 4000  # expand + rank + trim to markdown
```

**Ranking in pack:**
- 0.5 × RRF score (from search)
- 0.3 × degree-centrality (well-connected nodes rank higher)
- 0.2 × recency (recently updated nodes rank higher)

**Output:** Markdown. Each node carries `sources` for citation.

**Examples:**
- "What did we decide about dedup thresholds?"
- "Who proposed RRF fusion?"
- "What tools does the team use?"

**When to escalate:** If search returns >50 hits, suggest deep-search instead.

---

## 2. NL-Cypher

**For:** structural, aggregation, or path queries that the keyword/hybrid index handles poorly.

**Pipeline:**

```
kg cypher "<generated Cypher>"
```

> **Deferred to M2:** `kg cypher` is not yet implemented in M1; the command will exit with an error.

**Allowed Cypher clauses:** `MATCH`, `OPTIONAL MATCH`, `WHERE`, `WITH`, `RETURN`, `ORDER BY`, `LIMIT`.
**Forbidden (engine rejects):** `CREATE`, `MERGE`, `SET`, `DELETE`, `CALL`.

The skill reads `ontology.json` to generate valid type labels and property names. Edge types from the ontology become relationship types in Cypher.

**Examples:**
- "All organizations founded before 2020" →
  `MATCH (o:organization) WHERE o.attributes.founded_year < 2020 RETURN o.name, o.summary`
- "Path from person X to technology Y" →
  `MATCH path = (p:person {name: "X"})-[*1..3]-(t:object {name: "Y"}) RETURN path`
- "Count of nodes by type" →
  `MATCH (n) RETURN n.type, count(n) ORDER BY count(n) DESC`

**When to use:** The question asks about structure (paths, counts, type constraints, pattern matching) rather than content.

---

## 3. Deep-search

**For:** exploratory, broad, or "everything about X" questions. Materializes a wiki section for progressive disclosure.

**Pipeline:**

```
kg wiki build --from-query "<question>" --hops 3
```

> **Deferred to M4:** `kg wiki build` is not yet implemented in M1; the command will exit with an error.

This creates `wiki/deep/<slug>/` containing:
- `index.md` — summary of all entities and their connections
- `entities/<entity-slug>.md` — per-entity pages with full summaries, attributes, and cross-links

**Caching:** Keyed by query slug + graph version. Stale on version bump (after any `kg save` that changes the graph).

**Progressive disclosure:**
1. Read `index.md` first — this is the overview.
2. Drill into specific entity pages as needed.
3. Raw source files are last resort only (explicit opt-in, not automatic).

**Examples:**
- "Give me everything we know about the RRF fusion decision"
- "Comprehensive review of all tools and preferences in the graph"
- "All context on the AlphaFold work"

**When to use:** Hybrid returns too many hits, or the user explicitly asks for comprehensive coverage.

---

## Mode selection decision tree

```
Question received
├─ Asks about structure (paths, counts, patterns)?
│  └─ YES → NL-Cypher
├─ Asks for comprehensive / exploratory coverage?
│  └─ YES → deep-search
└─ Otherwise → hybrid (default)
```

Override: the user can say "use Cypher" or "deep search" to force a mode.