# KG: A Portable Unified Memory Layer for Any Harness

**Design document · v0.1 draft**

A local-first, per-project unified memory built on markdown (raw), vectors (semantic), and a knowledge graph (POLE+O) — served to any harness (Claude Code, Codex, OpenCode, Cursor, Pi, …) through **skills (first-class)**, **MCP (standards bridge)**, and a **CLI (consistent command surface)**.

---

## 0. Source Consolidation

What this design borrows, and from where:

| Source | What we take |
|---|---|
| *Agent Memory From Scratch* (Decoding AI) | End-to-end architecture: ontology-as-contract, write path (7 stages), single-collection graph store, 3 read primitives (graph search / deep search / NL query), MCP serving with agent-shaped primitives (never raw DB ops), conversation-ingestion hook for continual learning, content-derived idempotent IDs |
| *Keep Your Knowledge Graph Clean* + diagram 1 | The entity-normalization algorithm: **resolution (naming) ≠ deduplication (identity)**; exact → fuzzy → semantic short-circuit chain; 0.7·embed + 0.3·fuzzy blended score; ≥0.95 merge / 0.85–0.95 flag `same_as` pending / <0.85 new node; tombstoning; the nightly **dream pipeline** over recently-ingested nodes; checkpoints + retries at every LLM stage |
| *Ontology* diagram 2 | POLE+O nouns (Person, Object, Location, Event, Organization) + `preference` (with `superseded_by`) + `fact` (edge-less island, similarity-only) + structural nodes `document`/`chunk` with `part_of`/`next`; Data + Ontology → Extraction → KG instances |
| *Unified Memory* diagram 3 | The full loop: Harness ↔ MCP tools (3 read: nl_query / query / deep_search; 3 write: ingest_conversation / ingest_file / ingest_url) → query planner (map NL → query, find K seed nodes, expand to connected nodes) → subgraph → rerank + top-K → pack into context; deep search → **LLM wiki** (`index.md`, `sources/`, `entities/`, `preferences/`, `facts/`); ingestion side (data pipeline, registry, memory pipeline) → KG objects → store |
| *The Context Layer* | Portability doctrine: the harness is disposable; own the memory + business logic; serve via MCP *and* skills; single engine that does text + vector + graph beats three databases; temporality via `valid_from`/`valid_until` + `superseded_by` instead of an append-only log; build levels 1/2/3 |
| *LLM Wikis as Agent Memory* + Karpathy gist + OKF | Files-first fallback and query cache: `raw/` immutable, `wiki/` derivatives, one index the agent reads first; progressive disclosure (index → source page → derivatives → raw); wiki grows from questions; scope per project (PARA); lint pass for orphans/contradictions; Obsidian-flavored `[[links]]` give a free graph view |
| **codebase-memory-mcp** (DeusData) | The engineering shape we reuse: single binary/package exposing **MCP stdio server + CLI + daemon** together; SQLite graph storage (nodes, edges, traversal, search, Louvain communities); read-only **Cypher subset** engine; multi-pass indexing pipeline; **content-hash (XXH3) incremental re-index**; committed compressed snapshot (`graph.db.zst`) so teammates bootstrap without re-indexing; local **graph visualization UI** served on localhost; one install command auto-configures MCP entries + instruction files + hooks across many agent surfaces |
| llm-wiki ecosystem (praneybehl plugin, nvk/llm-wiki, vanillaflava, ishicm) | Local-first retrieval stack validated in the wild: FastEmbed + SQLite vector search + incremental derived indexes under plain markdown; typed links in frontmatter as a lightweight graph; RRF ranking; query-lite read-only profiles; ship as Claude Code plugin / Codex plugin / AGENTS.md for everything else |

---

## 1. Goals & Non-Goals

**Goals**

1. **Own the context layer.** Memory survives any harness switch; swapping harness is a config pointer, not a migration.
2. **Local-first, per-project.** Everything lives in `<project>/.kg/`. No cloud dependency, no daemon required for the file layer. Git-friendly.
3. **Three synchronized representations** of one memory: markdown (human + LLM-wiki), vector index (semantic), graph store (POLE+O KG).
4. **Skills are the intelligence; MCP/CLI are the plumbing.** The harness LLM does extraction, judgment calls, consolidation (you already pay for it via your subscription). The CLI/MCP does deterministic work: storage, matching math, embeddings, search, thresholds. This is the hybrid the *Agent Memory* article calls out for Pulse: "leverage my Claude subscription for extraction and dedup, built as a hybrid between skills and Python modules."
5. **Pluggable databases** behind one storage interface, SQLite default.

**Non-goals (v1)**

- Multi-tenant / server deployment (design keeps `user_id` in IDs so it's not foreclosed).
- Append-only event log with full temporal replay — we take the article's advice: "think three times." We get temporality from `valid_from`/`valid_until`/`superseded_by` instead.
- A reranker model. RRF only, per the article: "20 lines of Python, needs no model."

---

## 2. Architecture Overview

Three planes. The knowledge plane is data you own; the engine plane is deterministic code; the interface plane is how any harness reaches it.

```
┌──────────────────────────── HARNESS (disposable) ────────────────────────────┐
│  Claude Code · Codex · OpenCode · Cursor · Pi · Gemini CLI · anything        │
│                                                                              │
│  SKILLS (first-class intelligence)          hooks (continual learning)       │
│  /kg:ingest /kg:extract /kg:query /kg:dream  ── session-end → ingest-conv    │
└───────────────┬──────────────────────────────────────────┬───────────────────┘
                │ shell (preferred in CLI harnesses)        │ MCP stdio (bridge)
┌───────────────▼──────────────────────────────────────────▼───────────────────┐
│                          ENGINE PLANE — `kg` package                         │
│                                                                              │
│   CLI (source of truth)  ◄── same core lib ──►  MCP server (thin wrapper)    │
│   kg init/status/save/resolve/embed/dedup/      tools = agent-shaped         │
│   search/expand/wiki/dream-candidates/viz       primitives, never raw DB ops │
│                                                                              │
│   Deterministic services: chunker · resolver (exact/fuzzy/semantic) ·        │
│   embedder (local ONNX default, API pluggable) · dedup scorer · RRF fusion · │
│   graph traversal · FTS/BM25 · wiki materializer · viz server · registry     │
└───────────────┬───────────────────────────────────────────────────────────────┘
                │ storage interface (adapter per backend)
┌───────────────▼───────────────────────────────────────────────────────────────┐
│                      KNOWLEDGE PLANE — <project>/.kg/                         │
│                                                                               │
│  raw/        markdown mirrors of every source (immutable)                     │
│  wiki/       LLM-wiki derivatives + deep-search caches (OKF-shaped)           │
│  kg.db       SQLite: nodes + edges (graph) · FTS5 (BM25) · sqlite-vec (ANN)   │
│  registry.jsonl · ontology.json · config.toml · snapshots/kg.db.zst           │
└───────────────────────────────────────────────────────────────────────────────┘
```

**Key inversion vs. the article's Level-1 build:** the article's memory pipeline runs LLM extraction inside an orchestrated server-side pipeline. Here, extraction runs **inside the harness** guided by the `kg:extract` skill (your requirement: "no mcp, cli" for extraction intelligence). The CLI/MCP only accepts already-extracted POLE objects and runs the deterministic normalization gate before persisting. That keeps the engine model-free, cheap, and portable, and makes any harness's frontier model your extractor.

---

## 3. Storage Layout (per project, local-first)

```
<project>/
└── .kg/
    ├── config.toml              # backend choice, embedder, thresholds, scope
    ├── ontology.json            # the contract (JSON Schema, see §5) — versioned
    ├── registry.jsonl           # source registry: one line per ingested source
    ├── raw/                     # ── markdown folders for raw ──
    │   ├── 2026-07-26--article--keep-kg-clean.md
    │   ├── 2026-07-26--pdf--q3-report.md
    │   └── conversations/2026-07-26--claude-code--sess-91f2.md
    ├── wiki/                    # LLM-wiki layer (OKF-shaped, Obsidian-friendly)
    │   ├── index.md             # catalog the agent reads first
    │   ├── sources/             # one expanded summary page per raw source
    │   ├── entities/            # one page per canonical entity (mirror of graph)
    │   ├── facts/               # atomic fact pages
    │   ├── preferences/         # preference pages
    │   ├── notes/               # derivatives born from questions
    │   └── log.md               # every question/answer/change appended
    ├── kg.db                    # default backend: SQLite (graph + FTS5 + vec)
    ├── snapshots/kg.db.zst      # committed compressed snapshot (team bootstrap)
    └── review/same_as.md        # human review queue rendered as checklist
```

Design rules:

- **`raw/` is immutable and append-only.** Every source — PDF, DOCX, URL, pasted text, conversation — is normalized to markdown with YAML frontmatter (source URL/path, sha256, ingested_at, type, title). Re-ingesting the same content hash is a no-op (idempotent, registry-checked). Your second brain (Obsidian etc.) is never written to; it is referenced *into* `raw/` — the PARA/read-only-snapshot rule from the wiki article.
- **`kg.db` is derived state.** It can always be rebuilt from `raw/` by re-running `/kg:extract`. This is the cheap substitute for the append-only log: raw + registry *are* the log; the DB is the materialized view. Bad extraction? Delete affected nodes by lineage, re-extract the raw file.
- **`wiki/` is a cache with value.** Entity pages are regenerated from the graph (`kg wiki sync`), deep-search wikis are materialized on demand, notes accrete from questions. Cross-references use `[[wikilinks]]` so Obsidian renders the graph for free.
- **Git policy:** commit `raw/`, `wiki/`, `registry.jsonl`, `ontology.json`, `config.toml`, `snapshots/kg.db.zst`; ignore `kg.db`. Teammates clone → `kg init --from-snapshot` → instantly warm (the codebase-memory-mcp snapshot trick). Vectors are re-embedded lazily if the embedder differs.

---

## 4. Database Choices (pick-your-db, local first)

One storage interface, three capabilities (graph, lexical, vector), adapters per backend:

| Backend | Graph | BM25/keyword | Vector | Notes |
|---|---|---|---|---|
| **SQLite (default)** | `nodes`/`edges` tables + recursive CTE traversal (codebase-memory-mcp pattern) | FTS5 | `sqlite-vec` | Zero-dependency, single file, embeds everywhere. Default because "the simplest system that works wins." |
| Kùzu / LadybugDB | native Cypher | via extension | native | For users who want real Cypher + columnar graph perf; the article's Pulse uses LadybugDB |
| LanceDB + SQLite | SQLite graph | Lance FTS | Lance | When vectors dominate (100k+ chunks) |
| MongoDB | `$graphLookup` | `$text` | `$vectorSearch` | The article's production path; team/shared deployments |
| Neo4j | native | — | native | **Exploration/viz sidecar only**, synced from primary — never primary (article's advice: use it past 3–4 hops or for visualization) |

Adapter interface (all the engine ever calls):

```
upsert_nodes(nodes) · upsert_edges(edges) · get(id) · delete(id, tombstone=True)
neighbors(ids, depth, direction, edge_types) -> subgraph
fts_search(query, k, type_filter) -> ranked ids
vec_search(embedding, k, type_filter) -> (id, cosine) list
cypher_read(query) -> rows          # read-only Cypher subset, SQLite via translator
```

**Embeddings, local-first:** default `bge-small-en-v1.5` (or `voyage-3.5`-compatible dims if API configured) via FastEmbed/ONNX — no API key required, matching the llm-wiki-plugin v3 stack. `config.toml` swaps in Voyage/OpenAI/Gemini. Two embedding uses, per the clean-graph article: **light name-only embeddings** for resolution's semantic match, **full-context embeddings** (name + type + subtype + summary + high-signal attributes per type — never IDs) for dedup and semantic search.

---

## 5. Data Model — The Ontology Is the Contract

`ontology.json` is generated once from Pydantic (`model_json_schema()`) and injected into **both** the extract skill (writer) and the query skill / NL-query translator (reader). One artifact, two consumers, no drift.

### 5.1 Node types

```
POLE+O entities : person · organization · location · event · object
                  each with optional `subtype` (domain refinement, e.g.
                  object: software | document | task | topic | project)
personalization : preference   (typed slots: category, subject, polarity,
                                strength, valid_from, valid_until)
                  fact         (atomic S-P-O triplet; an ISLAND — no edges,
                                reachable only by similarity/keyword)
structural      : document · chunk   (created by code, not the LLM)
short-term      : conversation · session
reasoning (opt) : agent · tool_call · memory
```

### 5.2 Node schema (common)

```json
{
  "id": "{user_id}:{type}:{canonical_name_slug}",
  "type": "person", "subtype": "individual",
  "name": "extracted surface form",
  "canonical_name": "Demis Hassabis",
  "aliases": ["Demis Hassabis, CEO", "D. Hassabis"],
  "summary": "1–3 sentence LLM summary",
  "attributes": { "...typed per ontology..." },
  "embedding": "[full-context vector]",
  "valid_from": null, "valid_until": null,
  "sources": [{"doc": "raw/....md", "chunk": 3}],   // lineage: refs, not copies
  "created_at": "...", "updated_at": "...",
  "status": "active | tombstoned"
}
```

### 5.3 Edge schema

```json
{
  "id": "{source_id}|{semantic_type}|{target_id}",       // content-derived → idempotent
  "type": "related_to",
  "semantic_type": "employed_by",   // knows, member_of, owns, uses, located_at,
                                    // resides_at, alias_of, has_task, ...
  "summary": "one-line evidence", "confidence": 0.0,
  "sources": [...], "valid_from": null, "valid_until": null
}
```

Structural edges (code-inferred, never LLM): `part_of` (chunk→document), `next` (chunk→chunk), `mentions` (chunk→entity), `same_as {status: pending|confirmed|rejected, confidence}`, `superseded_by` (preference→preference).

Content-derived IDs make **every write idempotent** — re-running extraction on the same raw file cannot duplicate the graph.

---

## 6. Interface Plane

### 6.1 Priority order

1. **Skills — first-class.** All intelligence and orchestration. Portable as: Claude Code plugin (`/kg:*` commands + hooks), Codex prompts/plugin, OpenCode instructions, Cursor rules, and a fallback `AGENTS.md` that inlines the skill contract for anything else (the nvk/llm-wiki packaging pattern).
2. **MCP — the standards bridge.** One FastMCP stdio server, a thin wrapper over the same core library the CLI uses. For harnesses/GUIs where shell access is awkward (Claude Desktop, IDE-integrated agents), and for MCP Resources (expose `wiki/index.md`, ontology) and a future MCP App (graph visualizer).
3. **CLI — the consistent command surface.** Humans and skills share it. In CLI harnesses, skills call `kg …` directly via shell — fewer moving parts than MCP, and the tool contract is identical.

### 6.2 CLI commands (= MCP tools, same names, same core functions)

```
kg init [--backend sqlite|kuzu|lancedb|mongo] [--from-snapshot]
kg status                       # sources, node/edge counts, pending reviews, staleness

# ingest plumbing (called by /kg:ingest)
kg raw add <path|url|-> [--type pdf|docx|md|url|text|conversation] [--title ...]
                                # converts → markdown → raw/, registers, dedupes by hash
kg raw list [--unextracted]     # what /kg:extract should read

# save plumbing (called by /kg:extract) — the ONLY write path into the graph
kg save --nodes nodes.json --edges edges.json --source raw/<file>#chunk-<n>
        # runs the full normalization gate (§8): validate → resolve → embed
        # → dedup → merge/flag/new → upsert; prints a per-entity decision report
kg resolve "<name>" --type person          # dry-run resolution (skills can consult)
kg dedup-check <node.json>                 # dry-run dedup score

# query plumbing (called by /kg:query)
kg search "<query>" [--mode hybrid|semantic|bm25|keyword] [--type ...] [-k 10]
kg expand <node-id ...> [--hops 2] [--direction both] [--edge-types ...]
kg cypher "<read-only query>"              # Cypher-subset, validated, read-only
kg pack <subgraph.json> [--budget 4000]    # rank + trim subgraph to token budget
kg wiki build [--from-query "<q>" --hops 3]   # deep search → materialize wiki/
kg wiki sync                                   # regenerate entities/ facts/ prefs/ from graph
kg wiki lint                                   # orphans, broken links, contradictions

# dream plumbing (called by /kg:dream)
kg dream candidates [--since last-dream]   # emits worklist (§10): recent-node
                                           # dedup pairs, pending same_as, expiring
                                           # validity, orphan facts, contradiction sets
kg merge <id-a> <id-b> [--tombstone-b]     # deliberate merge (skill/human-invoked)
kg review list|confirm|reject <same_as-id> # human/agent gray-zone queue

kg viz [--port 9749]                       # local graph UI (codebase-memory pattern)
kg snapshot                                # write snapshots/kg.db.zst
kg install [claude-code|codex|opencode|cursor|all]
                                           # auto-configure: skills + MCP entry +
                                           # instruction stanza + session hooks
```

MCP exposes the agent-shaped subset (never raw DB ops): `ingest_url`, `ingest_file`, `ingest_text`, `ingest_conversation`, `save_pole`, `query_memory`, `nl_query_memory`, `deep_search_memory`, `dream_candidates`, `review_same_as` — plus Resources for `wiki/index.md` and `ontology.json`.

---

## 7. The Four Skills (main flow)

Each skill = `SKILL.md` (workflow + when-to-trigger, "pushy" description) + `references/` (ontology contract, extraction prompt, few-shots, output schemas) + `scripts/` where deterministic. SKILL.md stays < 500 lines; progressive disclosure for the rest.

### 7.1 `/kg:ingest` — get sources into `raw/`

**Input:** pdf | docx | md | url | text | (conversation via hook).
**Who does what:** harness may fetch/convert when it has the tools (e.g., reading a PDF it can already see); otherwise the skill calls `kg raw add`, whose converters (markitdown-class: pdf/docx/html→md, trafilatura for URLs) normalize to markdown.

```
1. Identify source type; check `kg status` / registry for prior ingestion (hash).
2. Convert to markdown with frontmatter {source, sha256, type, title, ingested_at}.
3. `kg raw add …`  → file lands in raw/, registry line appended.
4. Write/refresh the one-line catalog entry in wiki/index.md.
5. Report: what was added, what was skipped as duplicate, suggest `/kg:extract`.
```

No extraction here. Ingest is cheap and safe to run in bulk.

### 7.2 `/kg:extract` — raw → POLE + facts + embeddings (harness intelligence)

**Input:** nothing (= all unextracted raw, from `kg raw list --unextracted`) or specific file(s).
**The skill guides the harness LLM; only the final save touches CLI/MCP.**

```
For each raw file:
1. Chunk: 512 tokens, 64 overlap (skill script or harness-native reading;
   `kg raw add` pre-computes chunk boundaries into frontmatter).
2. Per chunk, prompt the harness model with:
   - the chunk text (one chunk, no IDs, no prior state — batchable to 1M+ units)
   - ontology.json (the contract)
   - output schema: {nodes:[{type,subtype,name,summary,attributes}],
                     edges:[{source_name,semantic_type,target_name,summary}],
                     facts:[{subject,predicate,object}],
                     preferences:[...]}
   Edge endpoints reference node `name`s within the same record.
3. Validate JSON against schema (skill script). On failure → retry with the
   validator error appended (max 2 retries), then checkpoint the chunk as
   failed and continue (diagram 1's Retry/Checkpoint! boxes).
4. `kg save --nodes … --edges … --source raw/<f>#chunk-<n>`
   → engine runs the deterministic normalization gate (§8) and reports
     per-entity: RESOLVED-TO / MERGED / FLAGGED / NEW.
5. After the file: `kg wiki sync --touched` regenerates affected entity pages;
   mark the registry entry extracted; checkpoint progress (resumable).
6. Surface the gray-zone list ("2 pairs flagged for review — run
   /kg:dream or `kg review list`").
```

Cost note: extraction is the expensive step and it rides the harness subscription. A future `--cascade` mode can pre-filter with spaCy/GLiNER before the LLM (the article's cost-tiered cascade), but v1 keeps it pure-LLM.

### 7.3 `/kg:query` — read the memory

**Modes, matching the diagram-3 read side:**

```
a. hybrid (default "graph search"):
   kg search --mode hybrid          # FTS5(BM25) ∥ sqlite-vec, fuse ranks with
                                    # RRF (k=60) → top-10 seed nodes
   kg expand <seeds> --hops 2       # bidirectional traversal = the GraphRAG step
   kg pack --budget N               # dedupe, rank by (RRF ∪ centrality ∪ recency),
                                    # trim to token budget → inject into context
b. semantic | bm25 | keyword:      single-index variants of `kg search`
c. graph traverse:                 kg expand / kg cypher (read-only, validated —
                                   the "NL query" mode: skill maps the user's
                                   question to Cypher using ontology.json,
                                   engine rejects any write clause)
d. deep search (50+ results or exploratory):
   kg wiki build --from-query "…" --hops 3
   → materializes a scoped wiki under wiki/deep/<slug>/ with index.md,
     entity pages, cross-links; the skill then answers via progressive
     disclosure: index → source page → derivatives → raw (last resort).
     Cache is kept and re-synced or discarded per config.
```

The skill also **writes back**: every substantive answer appends a note to `wiki/notes/` and a line to `wiki/log.md` — the wiki grows from questions (Karpathy pattern), giving continual learning even on the read path.

### 7.4 `/kg:dream` — consolidate, update, clean

The dream pass from the clean-graph article, upgraded from "nightly cron" to an on-demand skill (plus optional session-end hook). **Engine proposes, harness judges, engine executes.**

```
1. kg dream candidates --since last-dream  → worklist:
   • RECENT-PAIR : cross-dedup of nodes ingested in parallel that never saw
     each other (embeddings already computed → cheap, DB-only)
   • PENDING     : same_as edges awaiting judgment
   • EXPIRING    : preferences/facts past valid_until, or superseded chains
   • ORPHAN      : facts/entities with no sources after raw deletion; broken lineage
   • CONTRADICT  : fact pairs with same subject+predicate, conflicting objects
   • WIKI-LINT   : output of `kg wiki lint` (orphan pages, broken [[links]])
2. For each item, the harness LLM judges with full node context:
   merge? (kg merge a b) · reject (kg review reject) · supersede
   (write superseded_by edge, set valid_until) · retire (tombstone) ·
   keep-both · escalate to human (leave in review/same_as.md).
   Auto-merge stays reserved for engine-scored ≥0.95; the model may only
   *confirm* gray-zone pairs, never bulk-merge below threshold — a wrong
   merge is the one unrecoverable mistake.
3. New knowledge: while traversing, the model may emit *new* facts/relations
   it can infer across sources (e.g., two docs jointly imply employed_by) —
   saved through the same `kg save` gate, tagged source: dream/<date>.
4. kg wiki sync && kg snapshot; write a dream report to wiki/log.md.
```

Tombstoned nodes stay queryable for forensics but are excluded from matching. Undoing a merge = re-extract the source lineage — which raw/ makes possible.

---

## 8. The Normalization Gate (inside `kg save`)

Deterministic re-implementation of diagram 1, per entity:

```
entity in
 ├─ 1. VALIDATE against ontology.json (reject → error to caller)
 ├─ 2. RESOLUTION — "what should we call this?"  (names only, type-gated)
 │     alias-list hit → exact (normalized casing/whitespace)
 │     → fuzzy (token-based, RapidFuzz, ≥0.85)
 │     → semantic (name-only light embedding, ≥0.80)
 │     short-circuit chain; match → canonical_name = matched name,
 │     surface form appended to aliases; no match → canonical_name = extracted
 │     name. NO merges here.
 ├─ 3. EMBED — full-context vector (name+type+subtype+summary+high-signal
 │     attrs per type; never identifiers)
 ├─ 4. DEDUPLICATION — "is this the same real-world entity?"
 │     candidates = same-type nodes (ANN top-k ∪ same canonical_name)
 │     score = 0.7·cosine(full-context) + 0.3·fuzzy(full-context text)
 └─ 5. ROUTE
       score ≥ 0.95 → MERGE into existing (union aliases/sources/attrs,
                       re-embed, re-point edges)
       0.85–0.95    → NEW node + same_as{status:pending,confidence} edge
                       → review queue (never auto-merge the gray zone)
       < 0.85       → NEW node
```

Edges are upserted after both endpoints settle; endpoint names re-map to post-merge IDs. Facts skip steps 2/4-graph-matching (islands) but are embedded and near-duplicate-checked against other facts. Every stage checkpoints per chunk so a crashed run resumes.

Why the two-step split is non-negotiable: resolution absorbs "NYC"→"New York City" and typo variants for grouping/analytics; dedup, on richer signal, is what keeps Paris-France ≠ Paris-Texas and CEO-Jensen ≠ doctor-Jensen from silently fusing. Collapsing them into one fuzzy check is the failure mode that rots graphs.

---

## 9. Visualization (reused from codebase-memory-mcp)

`kg viz` serves a local UI (default `localhost:9749`-style single port) straight off the graph store, alongside — not inside — the MCP server:

- Force-directed 2D/3D graph (force-graph/three.js) of nodes colored by POLE+O type, sized by degree; edge labels = `semantic_type`.
- **Louvain community detection** shipped in the engine (the codebase-memory store does this in SQLite) to reveal topic clusters; cluster view ↔ node view drill-down.
- Filters: type/subtype, time window (`valid_from/valid_until`), source document, pending `same_as` overlay (review visually).
- Click node → side panel renders its `wiki/entities/<slug>.md` page — the wiki and the viz are two views of one graph.
- Zero extra infra: reads `kg.db` directly; also usable over a snapshot. Obsidian remains the free fallback viewer for the `wiki/` layer, and a Neo4j export (`kg export --cypher`) remains the power-user path.

---

## 10. Portability Matrix

| Harness | Skills delivery | Transport | Hook for continual learning |
|---|---|---|---|
| Claude Code | plugin: 4 skills + `kg` in PATH | shell (CLI) primary; MCP optional | `SessionEnd`/`Stop` hook → `kg raw add --type conversation` + incremental every ~10 turns |
| Codex CLI | plugin/prompts port of same SKILL.md | shell primary; MCP | session-end wrapper script |
| OpenCode | instruction file + skills dir | shell; MCP | plugin event |
| Cursor | rules file summarizing skill contract | MCP primary (agent mode) | manual `/kg:ingest conversation` or MCP `ingest_conversation` |
| Anything else | generated `AGENTS.md` (inlined contract) | MCP stdio | manual |

`kg install <harness>` writes all of the above automatically (MCP entry, instruction stanza, hooks) — the codebase-memory-mcp one-command auto-config pattern. Because skills are markdown + a stable CLI contract, the business logic never couples to one harness's keywords — which answers the context-layer article's closing worry ("your data can move between harnesses, but can your skills?"): the skills' only hard dependency is `kg` itself.

---

## 11. Config (`.kg/config.toml`)

```toml
[project]
scope = "my-coding-agent"          # per-project memory; no cross-project bleed
user_id = "quan"

[backend]
kind = "sqlite"                    # sqlite | kuzu | lancedb | mongo
path = ".kg/kg.db"

[embedding]
provider = "local"                 # local | voyage | openai | gemini
model = "bge-small-en-v1.5"        # 384d local default; voyage-3.5 @1024d if API
embed_fields = { person = ["name","summary","attributes.role","attributes.email"],
                 object = ["name","summary","attributes.model"] }  # high-signal only

[thresholds]
resolve_fuzzy = 0.85
resolve_semantic = 0.80
dedup_merge = 0.95
dedup_flag = 0.85
dedup_weights = { embedding = 0.7, fuzzy = 0.3 }

[chunking]
tokens = 512
overlap = 64

[query]
rrf_k = 60
default_hops = 2
deep_search_hops = 3
pack_budget_tokens = 4000

[dream]
recent_window = "since-last-dream"
auto_hook = false                  # optional session-end mini-dream
```

---

## 12. Failure Modes & Safeguards

- **Wrong merge (unrecoverable):** gray zone never auto-merges; merges log both pre-images to `wiki/log.md`; lineage in `raw/` allows re-extraction to rebuild.
- **Ontology drift / invented relation types** (the `part_of` vs `Part Of` vs `part of` disaster): the extractor may only emit types present in `ontology.json`; `kg save` rejects everything else. Ontology changes are explicit, versioned edits followed by `kg reextract --affected`.
- **NL query abuse:** `kg cypher` parses to an AST and rejects non-read clauses; NL mode is read-only by construction.
- **Parallel-ingest blind spots:** covered by dream's RECENT-PAIR sweep.
- **Wiki rot:** `kg wiki lint` in dream (orphans, broken links, stale claims, contradictions) — the concept-confusion/superficiality antidote from the wiki article.
- **RAM/index bloat:** only `kg.db` is indexed; `raw/` stays cold on disk (the index-only-the-view lesson).
- **Interrupted extraction:** per-chunk checkpoints in registry; `/kg:extract` resumes.

---

## 13. Build Plan

1. **M0 — files only (1 week-end):** `kg init/raw add/status`, converters, registry, wiki index. You already have a working LLM-wiki memory at this point (Level "Scrabble").
2. **M1 — graph + gate:** SQLite adapter (nodes/edges/FTS5/sqlite-vec), `kg save` with the full normalization gate, `kg search/expand/pack`, `/kg:extract` + `/kg:query` skills.
3. **M2 — dream + review:** `kg dream candidates`, merge/review commands, `/kg:dream` skill, snapshot.
4. **M3 — serving breadth:** FastMCP wrapper, `kg install` for the four harnesses, conversation hooks.
5. **M4 — polish:** `kg viz` (+Louvain), Cypher-subset reader, Kùzu/LanceDB adapters, `--cascade` extraction, deep-search wiki caching policies.

Build-vs-buy checkpoint: if M1's gate feels heavy, the escape hatch is Level-2 — keep the skills + CLI contract but back `kg save`/`kg search` with Graphiti or neo4j-labs/agent-memory. The interface plane is designed so the knowledge plane's backend is swappable without touching a single skill.

---

## 14. End-to-End Walkthrough

```
$ kg init && kg install claude-code
> /kg:ingest https://…/keep-knowledge-graph-clean  paper.pdf  notes.md
   → 3 files in raw/, indexed in wiki/index.md
> /kg:extract
   → 41 chunks → 87 nodes proposed → gate: 71 new, 9 merged, 4 resolved-alias,
     3 flagged (same_as pending)
> /kg:query "what did we decide about dedup thresholds, and who proposed RRF?"
   → hybrid search → 8 seeds → 2-hop expand → packed 3.2k tokens → answer
     with lineage; note appended to wiki/notes/
> /kg:dream
   → 3 pending pairs: 1 merged (0.93 + model-confirmed), 1 rejected
     (Codex-model ≠ Codex-CLI), 1 escalated to review/same_as.md;
     2 superseded preferences closed; 1 new cross-doc fact saved; snapshot ✓

# next week, on Codex:
$ kg install codex        # same .kg/, same memory, new harness. Done.
```

---

*Everything the harness learns flows back in; nothing you know lives in the harness.*
