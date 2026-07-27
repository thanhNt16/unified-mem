# Data model

`ontology.json` is the contract. The harness LLM (writer) and the query/NL-query
translator (reader) consume the same artifact — generated once from Pydantic via
`model_json_schema()`. One artifact, two consumers, no drift.

## Node types

```
POLE+O entities   person · organization · location · event · object
                  (optional subtype, domain-refined:
                   object: software | document | task | topic | project)
personalization   preference  (category, subject, polarity, strength,
                               valid_from, valid_until)
                  fact        (atomic S-P-O triplet; an ISLAND — no edges,
                               reachable only by similarity/keyword)
structural        document · chunk   (created by code, not the LLM)
short-term        conversation · session
```

A project may add `subtype` and `semantic_type` values; it may **not** add node
types. `kg save` validates against `ontology.json` and rejects unknown types —
the anti-drift gate.

## Node schema

```json
{
  "id": "{user_id}:{type}:{slug(canonical_name)}",
  "type": "person",
  "subtype": "individual",
  "name": "extracted surface form",
  "canonical_name": "Demis Hassabis",
  "aliases": ["Demis Hassabis, CEO", "D. Hassabis"],
  "summary": "1–3 sentence LLM summary",
  "attributes": { "...typed per ontology..." },
  "attribute_conflicts": [],
  "embedding": "[full-context vector]",
  "valid_from": null,
  "valid_until": null,
  "sources": [
    { "doc": "raw/2026-07-26--text--hassabis-note.md", "chunk": 3 }
  ],
  "created_at": "2026-07-27T...",
  "updated_at": "2026-07-27T...",
  "status": "active",
  "merged_into": null
}
```

### Field rules

- **`id`** — content-derived. Slug rule: lowercase, non-alphanumeric → `-`,
  collapse, trim, 80-char cap. Collision on same type+slug is intended.
- **`sources`** — lineage references (`raw/<file>#chunk-<n>`), never content copies.
- **`attribute_conflicts`** — appended on merge when attributes disagree; dream
  judges them later.
- **`embedding`** — full-context vector over the fields listed in
  `config.toml` `embedding.embed_fields` for that type (name + type + subtype +
  summary + high-signal attrs; never IDs).
- **`status`** — `active` or `tombstoned`. Tombstoned nodes carry
  `merged_into` pointing at the winner and remain resolvable by ID.
- A rejected dedup pair gets a `-2` (etc.) suffix **stored explicitly in the
  node** so IDs stay stable across re-extraction.

## Edge schema

```json
{
  "id": "{source_id}|{semantic_type}|{target_id}",
  "type": "related_to",
  "semantic_type": "employed_by",
  "summary": "one-line evidence",
  "confidence": 0.0,
  "sources": [
    { "doc": "raw/2026-07-26--text--hassabis-note.md", "chunk": 3 }
  ],
  "valid_from": null,
  "valid_until": null
}
```

`semantic_type` is the realized relationship vocabulary:
`knows · member_of · employed_by · owns · uses · located_at · resides_at ·
alias_of · has_task · superseded_by` and project-refined additions.

### Structural edges (code-inferred, never LLM)

| Edge | Meaning |
|---|---|
| `part_of` | chunk → document |
| `next` | chunk → chunk |
| `mentions` | chunk → entity |
| `same_as` | `status: pending \| confirmed \| rejected`, `confidence` |
| `superseded_by` | preference → preference (temporal) |

Content-derived IDs make every edge upsert idempotent — re-extracting the same
raw file cannot duplicate edges.

## Facts are islands

A `fact` node is an atomic S-P-O triplet. It has **no edges** — it is reachable
only by similarity or keyword search. This keeps facts first-class truth
candidates independent of the entity graph, and makes contradiction detection
(`kg dream candidates --kind CONTRADICT`) a query over facts sharing
subject+predicate, not a graph traversal.

## Storage adapter interface

All engine code calls this interface. v1 ships SQLite; the interface keeps other
backends (Kùzu, LanceDB, Mongo) a future PR.

```
upsert_nodes(nodes)
upsert_edges(edges)
get(id)
delete(id, tombstone=True)
neighbors(ids, depth, direction, edge_types) -> Subgraph
fts_search(query, k, type_filter) -> ranked ids
vec_search(embedding, k, type_filter) -> (id, cosine)[]
cypher_read(query) -> rows     # read-only Cypher subset → SQLite via translator
```

### SQLite implementation

- Tables `nodes`, `edges` + recursive-CTE traversal for `neighbors` (the
  codebase-memory-mcp pattern, reused here).
- `FTS5` (BM25) for keyword and hybrid search.
- `sqlite-vec` for approximate nearest-neighbor vector search.
- WAL mode, single writer. `kg save` takes a file lock; parallel extract
  processes queue on save.

## Embeddings — two uses, both local-first

| Use | Vector | Threshold |
|---|---|---|
| Resolution (semantic name match) | Name-only light vector | ≥ 0.80 |
| Dedup + semantic search | Full-context vector | merge ≥ 0.95, flag ≥ 0.85 |

Default provider is `local` (`bge-small-en-v1.5` via FastEmbed/ONNX) — no API
key. `config.toml` `embedding.provider` swaps in `voyage`, `openai`, or
`gemini`. Both vectors are cached by content hash; re-extraction costs zero
embed calls when text is unchanged.

## What to read next

- [Architecture overview](overview.md) — layered design, gray zone, tombstone.
- [Runbook](../ops/runbook.md) — snapshot/restore, dream/review, wiki sync.
