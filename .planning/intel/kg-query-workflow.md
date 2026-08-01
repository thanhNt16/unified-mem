# /kg:query Workflow Specification

## Overview

Query routes questions to the correct memory substrate. Three modes:
1. **Hybrid** (default): Short, factual questions via RRF search + expansion
2. **NL-Cypher**: Structural queries (paths, aggregations)
3. **Deep-search**: Broad exploratory questions via materialized wiki

**Principle:** Default to hybrid. Switch modes only when the question demands it.

## Command Signatures

```bash
# Main entry points
kg query "<question>" [--mode hybrid|nl-cypher|deep] [--k <n>] [--budget <tokens>]

# Component primitives
kg search "<query>" [--mode hybrid|bm25|semantic] [--type <type>] [--k <n>]
kg expand <seed_id> [<seed_id>...] [--hops <n>] [--json]
kg pack <(kg expand ...) [-b <budget_tokens>]
kg cypher "<cypher-query>" [--readonly]

# Deep-search
kg wiki build --from-query "<question>" [--hops <n>]
```

## Input Formats

### Natural Language Questions (default)

```bash
kg query "what do we know about Demis Hassabis?"
```

The query string is parsed for:
- **Entity names**: proper nouns, known IDs
- **Temporal constraints**: dates, "recent", "before 2024"
- **Structural keywords**: "path", "all", "between"
- **Topical keywords**: "decisions", "preferences", "facts"

### Structural Queries (NL-Cypher)

```bash
kg query "all organizations founded before 2020" --mode nl-cypher
kg query "path from Demis Hassabis to AlphaFold" --mode nl-cypher
```

### Codebase Queries

```bash
kg query "what functions in auth.py" --mode hybrid --type Function
```

Routes to codebase-memory-mcp if project is indexed.

## Processing Steps

### Mode Selection Algorithm

```python
def select_mode(question):
    keywords_structural = ["path from", "all", "every", "count", "aggregate"]
    keywords_broad = ["everything about", "comprehensive", "all context on", "deep dive"]

    if any(kw in question.lower() for kw in keywords_structural):
        return "nl-cypher"
    elif any(kw in question.lower() for kw in keywords_broad):
        return "deep-search"
    elif has_explicit_type(question):
        return "nl-cypher"  # type-constrained
    else:
        return "hybrid"  # default
```

### Hybrid Mode (90% path)

#### Step 1: Seed Search

```bash
kg search "Demis Hassabis DeepMind" --mode hybrid --k 10
```

Under the hood:
- FTS5/BM25 over node names + summaries
- sqlite-vec ANN over embeddings
- RRF fusion (k=60)
- Return top-10 with scores

Output format:
```
0.8947  u:person:demis-hassabis  Demis Hassabis
0.8723  u:organization:deepmind  DeepMind
0.8234  u:object:alphafold       AlphaFold
...
```

#### Step 2: Expand Context

```bash
kg expand u:person:demis-hassabis u:organization:deepmind --hops 2 --json
```

Algorithm:
- Bidirectional BFS via recursive CTE
- Cap: 300 nodes (`cfg.query.subgraph_cap`)
- If cap hit: keep highest-scoring frontier, log truncation

Output (JSON):
```json
{
  "nodes": [...],
  "edges": [...],
  "truncated": false,
  "node_count": 47,
  "edge_count": 89
}
```

#### Step 3: Pack to Budget

```bash
kg expand u:person:demis-hassabis --hops 2 --json | kg pack -b 4000
```

Ranking formula:
```
score = 0.5 * rrf_score + 0.3 * degree_centrality + 0.2 * recency
```

Output: Markdown context, trimmed to budget tokens.

#### Step 4: Answer with Lineage

Model sees packed context, must cite `sources` field for every claim.

```markdown
## Answer

Demis Hassabis co-founded DeepMind in 2010.

**Sources:**
- u:person:demis-hassabis (sources: [raw/markdown/abc123.md#chunk-3])
- u:organization:deepmind (sources: [raw/markdown/def456.md#chunk-1])
```

### NL-Cypher Mode

#### Step 1: Read Ontology

```bash
cat .kg/ontology.json
```

Output:
```json
{
  "node_types": ["person", "organization", "location", "event", "object", ...],
  "edge_types": ["employed_by", "member_of", "knows", "located_at", ...],
  "properties": {...}
}
```

#### Step 2: Generate Cypher (model-side)

```cypher
MATCH (p:person)-[:employed_by]->(o:organization)
WHERE p.name CONTAINS "Demis"
RETURN p.name, o.name, o.founded_year
ORDER BY o.founded_year DESC
```

#### Step 3: Validate & Execute

```bash
kg cypher "$(cat query.cypher)" --readonly
```

Engine checks:
- Only allowed clauses: `MATCH`, `WHERE`, `RETURN`, `OPTIONAL MATCH`, `WITH`, `ORDER BY`, `LIMIT`
- Reject: `CREATE`, `MERGE`, `SET`, `DELETE`, `CALL`
- Server-side AST validation

Output: Tabular results, no expand/pack needed.

### Deep-Search Mode

#### Step 1: Materialize Wiki

```bash
kg wiki build --from-query "everything about DeepMind history" --hops 3
```

Creates `wiki/deep/<slug>/`:
```
wiki/deep/deepmind-history/
├── index.md             # Summary index of all entities
├── entities/
│   ├── demis-hassabis.md
│   ├── deepmind.md
│   └── alphafold.md
└── .meta.json           # Cache key + graph version
```

Cache invalidation: Stale on graph version bump.

#### Step 2: Progressive Disclosure

Read order:
1. `index.md` → entity list + summaries
2. `entities/<slug>.md` → per-entity detail with cross-links
3. Raw sources (last resort, explicit only)

Default: Query never touches `raw/`.

## Output Artifacts

| Mode | Output | Format |
|---|---|---|
| **hybrid** | Markdown answer with sources | stdout |
| **nl-cypher** | Tabular results + optional markdown | stdout |
| **deep-search** | Markdown with wiki links + entities | stdout |

### Hybrid Output Schema

```json
{
  "answer": "Demis Hassabis co-founded DeepMind in 2010...",
  "sources": [
    {
      "node_id": "u:person:demis-hassabis",
      "source_path": "raw/markdown/abc123.md",
      "chunk_index": 3,
      "relevance_score": 0.89
    }
  ],
  "graph_subgraph": {
    "nodes": [...],
    "edges": [...]
  }
}
```

## Integration Points

### codebase-memory-mcp Integration

When project has codebase-memory index:

```bash
kg query "what does authenticate_user do" --mode hybrid
```

Detection: `kg status` shows `codebase_memory_project: <name>`.

Flow:
1. Search kg graph first (person/org/object nodes for functions)
2. If hit: query codebase-memory for code-level detail
3. Merge: kg graph lineage + code signature/embedding

Example merge:
```
u:object:authenticate-user (kg: object node)
  ↳ codebase-memory://function:authenticate_user (MCP)
    - language: python
    - cyclomatic: 4
    - called_by: [validate_session, login]
```

### Graphify Integration

When query touches conversation history:

```bash
kg query "what did we decide about auth last week" --mode hybrid
```

Flow:
1. Search kg graph for `decision`/`preference` nodes
2. If transcript is a node: expand to conversation nodes
3. Pull messages from Graphify storage
4. Surface relevant turns with timestamps

### AgentMemory Integration

When query is about session-level memory:

```bash
kg query "what did I say about X last session" --mode hybrid
```

Flow:
1. Detect session/observation keywords
2. Call `mcp__plugin_agentmemory_agentmemory__memory_recall(query=X)`
3. Cross-reference with kg `conversation` nodes
4. Merge results, deduplicate by content

### wiki Integration

Deep-search mode **materializes** wiki pages. Hybrid mode **reads** existing wiki pages if cache is warm.

```bash
kg query "Tell me about AlphaFold" --mode hybrid
# First checks wiki/entities/alphafold.md
# If stale or missing: falls back to live graph query
# If stale: regenerates wiki page post-answer
```

## Routing Decision Matrix

| Query Type | Mode | Substrate |
|---|---|---|
| "What do we know about X?" | hybrid | kg.db |
| "Path from A to B" | nl-cypher | kg.db (Cypher) |
| "All decisions about X" | hybrid | kg.db |
| "Functions in src/auth.py" | hybrid | codebase-memory |
| "What did I say about X?" | hybrid | AgentMemory + kg |
| "Everything about DeepMind" | deep-search | wiki/ + kg.db |
| "Count of orgs founded pre-2020" | nl-cypher | kg.db |
| "Code complexity in gate.py" | hybrid | codebase-memory |

## Data Flow Diagram

```
┌──────────────┐
│ User Query   │  "what do we know about Demis Hassabis?"
└──────┬───────┘
       │ 1. Mode selection
       ↓
┌──────────────────┐
│ Mode Router      │  → hybrid / nl-cypher / deep-search
└──────┬───────────┘
       │
       ├─ hybrid ──────────────────────┐
       │                                ↓
       │              ┌──────────────────────────┐
       │              │  Search (RRF)            │  BM25 + ANN → RRF k=60
       │              └──────┬───────────────────┘
       │                     ↓
       │              ┌──────────────────────────┐
       │              │  Expand (BFS)            │  hops=2, cap=300
       │              └──────┬───────────────────┘
       │                     ↓
       │              ┌──────────────────────────┐
       │              │  Pack (rank+trim)        │  budget=4000 tokens
       │              └──────┬───────────────────┘
       │                     ↓
       │              ┌──────────────────────────┐
       │              │  Model Answer            │  with source citations
       │              └──────────────────────────┘
       │
       ├─ nl-cypher ───────────────────┐
       │                                ↓
       │              ┌──────────────────────────┐
       │              │  Cypher Validation       │  AST check (read-only)
       │              └──────┬───────────────────┘
       │                     ↓
       │              ┌──────────────────────────┐
       │              │  Execute (sqlite-vec)    │  Tabular results
       │              └──────────────────────────┘
       │
       └─ deep-search ──────────────────┐
                                        ↓
                       ┌──────────────────────────┐
                       │  Wiki Build              │  materialize pages
                       └──────┬───────────────────┘
                              ↓
                       ┌──────────────────────────┐
                       │  Progressive Disclosure  │  index → entities → raw
                       └──────────────────────────┘
```

## Performance Budgets

| Mode | Latency Target | Token Budget |
|---|---|---|
| **hybrid** | <2s | 4000 tokens packed |
| **nl-cypher** | <1s | 2000 tokens (results) |
| **deep-search** | <30s (with build) | 8000 tokens |

## Error Handling

| Error | Action | User Message |
|---|---|---|
| **No seeds found** | Return empty context | "No matching entities found" |
| **Cap exceeded** | Truncate, log | "Subgraph truncated to top 300 nodes" |
| **Cypher violation** | Reject | "Read-only mode: write clause rejected" |
| **Wiki build fail** | Fall back to hybrid | "Wiki materialization failed; using live query" |
| **Embedding down** | BM25-only | "Semantic search unavailable; using keyword only" |

## Deferred Features (M2+)

- `kg cypher`: Full NL-to-Cypher (M2)
- `kg query --explain`: Return reasoning + path through graph
- `kg query --export`: Dump subgraph to JSON for analysis
- `kg query --multi-hop`: 3+ hop expansion with reasoning chains

## Dependencies

- **kg.db**: sqlite-vec + FTS5
- **codebase-memory-mcp**: For code substrate
- **Graphify**: For transcript substrate
- **AgentMemory**: For session memory
- **wiki**: Materialized views for deep-search
