# KG — Portable Unified Memory Layer (Implementation Spec)

**Date:** 2026-07-26
**Status:** Design approved, ready for implementation plan
**Source:** `unified-memory-layer-design.md` (v0.1 draft), refined through brainstorming

A local-first, per-project unified memory: markdown (raw), vectors (semantic), knowledge graph (POLE+O). Served to any harness (Claude Code, Codex, OpenCode, Cursor, + `AGENTS.md` fallback) through **skills (first-class intelligence)**, **MCP (standards bridge)**, and a **CLI (consistent command surface)**.

The harness LLM does extraction, judgment, and consolidation — paid for by the subscription. The engine (CLI/MCP) does deterministic work: storage, matching math, embeddings, search, thresholds. This is the hybrid the *Agent Memory* article calls out: leverage the harness subscription for extraction and dedup, built as a hybrid between skills and Python modules.

---

## 1. Decisions Locked in Brainstorming

| Decision | Choice |
|---|---|
| Spec scope | Whole design (M0–M4) in one spec; implementation plan carries all five milestones |
| Relationship to existing `graphify` skill | **Keep separate.** `graphify` = one-off graphs; `kg` = persistent per-project memory |
| Language / distribution | **Python, `uv tool install kg` / `uvx kg`** |
| Database backends in v1 | **SQLite only**, behind a storage adapter interface (other backends deferred) |
| Harness targets | Claude Code, Codex CLI, OpenCode, Cursor (+ `AGENTS.md` for the rest) |
| Cuts | **None.** viz + Louvain, Cypher subset, snapshot, cascade all in scope |
| Architecture | **Road A — one layered package** (`kg`): core / cli / mcp / skills / install |

### 1.1 Success criteria (what "good" means for v1)

1. **Harness swap works.** Same `.kg/` answers the same question on Claude Code *and* Codex. The portability claim, proven end-to-end.
2. **Graph stays clean.** After N ingests: no duplicate entities, no invented edge types, Paris-France ≠ Paris-Texas, CEO-Jensen ≠ doctor-Jensen.
3. **Answers beat plain grep.** `/kg:query` returns lineage-backed answers a raw file search misses (cross-doc, multi-hop).
4. **Cheap + fast enough.** Ingest+extract a ~50-page doc without hours of wall-clock or huge token spend; query returns in seconds.

---

## 2. Package Shape (Road A)

Single Python package `kg`. Hard module boundaries — `core` imports nothing from `cli`/`mcp`/`install`, so CLI and MCP can never drift (both call the same core functions).

```
kg/
  core/
    storage/   base.py (adapter interface)  sqlite.py (default)
    ontology.py         Pydantic models → JSON Schema (the contract)
    convert.py          pdf/docx/html/url/text → markdown frontmatter
    registry.py         registry.jsonl + per-chunk checkpoint state
    resolve.py          exact → fuzzy → semantic (name-only, type-gated)
    embed.py            FastEmbed/ONNX local default; API pluggable
    dedup.py            0.7·cosine + 0.3·fuzzy over candidate set
    gate.py             kg save: validate → resolve → embed → dedup → route
    search.py           FTS5 ∥ sqlite-vec, RRF fusion
    traverse.py         BFS expand (recursive CTE), centrality, subgraph cap
    pack.py             rank (RRF∪centrality∪recency) + trim to token budget
    cypher/             read-only Cypher subset → AST → SQL translator
    wiki.py             materializer, sync, lint, deep-search cache
    viz/                localhost graph UI + Louvain
  cli/         main.py + one module per command group (typer)
  mcp/         server.py  (FastMCP, agent-shaped tools only, wraps core)
  skills/      kg-ingest/ kg-extract/ kg-query/ kg-dream/
  install/     claude_code.py codex.py opencode.py cursor.py agents_md.py
```

**Rule of test:** `core` is tested directly and exhaustively; `cli` and `mcp` get smoke tests only (they are thin). This is the lever that keeps one user productive.

---

## 3. Storage Layout (per project, local-first)

```
<project>/
└── .kg/
    ├── config.toml              # backend, embedder, thresholds, scope
    ├── ontology.json            # JSON Schema contract — versioned, pinned to project
    ├── registry.jsonl           # one line per ingested source + chunk checkpoint state
    ├── raw/                     # immutable markdown mirrors of every source
    │   ├── 2026-07-26--article--<slug>.md
    │   ├── 2026-07-26--pdf--<slug>.md
    │   └── conversations/2026-07-26--claude-code--sess-<id>.md
    ├── wiki/                    # OKF-shaped, Obsidian-friendly
    │   ├── index.md  sources/  entities/  facts/  preferences/
    │   ├── notes/  deep/  log.md
    ├── kg.db                    # SQLite: graph + FTS5 + sqlite-vec (derived, gitignored)
    ├── snapshots/kg.db.zst      # committed compressed snapshot (team bootstrap)
    └── review/same_as.md        # human review queue rendered as checklist
```

**Rules:**
- `raw/` is immutable and append-only. Every source normalized to markdown with YAML frontmatter (`source`, `sha256`, `type`, `title`, `ingested_at`, chunk boundaries). Re-ingest by same content hash = no-op (idempotent, registry-checked). Second brain (Obsidian etc.) is never written to; it is referenced *into* `raw/`.
- `kg.db` is derived state, rebuildable from `raw/` via `/kg:extract`. raw + registry *are* the log; the DB is the materialized view. Bad extraction → delete affected nodes by lineage, re-extract the raw file.
- `wiki/` is a cache with value. Entity pages regenerated from graph (`kg wiki sync`); deep-search wikis materialized on demand; notes accrete from questions. Cross-refs use `[[wikilinks]]` so Obsidian renders the graph for free.
- **Git policy:** commit `raw/ wiki/ registry.jsonl ontology.json config.toml snapshots/kg.db.zst`; ignore `kg.db`.

---

## 4. Storage Adapter Interface

All `core` ever calls. v1 ships the SQLite adapter; the interface keeps other backends (Kùzu/LadybugDB, LanceDB, Mongo) a future PR.

```
upsert_nodes(nodes) · upsert_edges(edges)
get(id) · delete(id, tombstone=True)
neighbors(ids, depth, direction, edge_types) -> subgraph
fts_search(query, k, type_filter) -> ranked ids
vec_search(embedding, k, type_filter) -> (id, cosine)[]
cypher_read(query) -> rows          # read-only Cypher subset → SQLite via translator
```

**SQLite implementation:** `nodes`/`edges` tables + recursive-CTE traversal (codebase-memory-mcp pattern) + FTS5 (BM25) + `sqlite-vec` (ANN). Single file, zero-dependency, embeds everywhere. WAL mode, single writer.

**Embeddings, local-first:** default `bge-small-en-v1.5` via FastEmbed/ONNX — no API key. `config.toml` swaps Voyage/OpenAI/Gemini. **Two embedding uses:**
- **Name-only light vector** — for resolution's semantic match.
- **Full-context vector** (name+type+subtype+summary+high-signal attrs per `config.embed_fields`, never IDs) — for dedup and semantic search.

Both cached by content hash → re-extraction costs zero embed calls if text unchanged.

---

## 5. Data Model — Ontology Is the Contract

`ontology.json` is generated once from Pydantic (`model_json_schema()`) and injected into **both** the extract skill (writer) and the query skill / NL-query translator (reader). One artifact, two consumers, no drift.

### 5.1 Node types

```
POLE+O entities : person · organization · location · event · object
                  (optional subtype, domain-refined, e.g. object: software|document|task|topic|project)
personalization : preference  (typed slots: category, subject, polarity, strength,
                               valid_from, valid_until)
                  fact        (atomic S-P-O triplet; an ISLAND — no edges,
                               reachable only by similarity/keyword)
structural      : document · chunk   (created by code, not the LLM)
short-term      : conversation · session
```

v1 may add `subtype` and `semantic_type` values per project; it may **not** add node types. `kg save` validates against the file and rejects unknown types/relations — the anti-drift gate.

### 5.2 Node schema

```json
{
  "id": "{user_id}:{type}:{slug(canonical_name)}",
  "type": "person", "subtype": "individual",
  "name": "extracted surface form",
  "canonical_name": "Demis Hassabis",
  "aliases": ["Demis Hassabis, CEO", "D. Hassabis"],
  "summary": "1–3 sentence LLM summary",
  "attributes": { "...typed per ontology..." },
  "attribute_conflicts": [],        // appended on merge conflict, dream judges
  "embedding": "[full-context vector]",
  "valid_from": null, "valid_until": null,
  "sources": [{"doc": "raw/....md", "chunk": 3}],   // lineage refs, never copies
  "created_at": "...", "updated_at": "...",
  "status": "active | tombstoned",
  "merged_into": null                // tombstone pointer, kept resolvable by ID
}
```

**Slug rule:** lowercase, non-alnum → `-`, collapse, trim, 80-char cap. Collision on same type+slug is *intended* — that IS the resolution hit. A rejected dedup pair gets `-2` (etc.) suffix stored explicitly in the node so IDs stay stable.

### 5.3 Edge schema

```json
{
  "id": "{source_id}|{semantic_type}|{target_id}",   // content-derived → idempotent
  "type": "related_to",
  "semantic_type": "employed_by",   // knows, member_of, owns, uses, located_at,
                                    // resides_at, alias_of, has_task, ...
  "summary": "one-line evidence", "confidence": 0.0,
  "sources": [...], "valid_from": null, "valid_until": null
}
```

Structural edges (code-inferred, never LLM): `part_of` (chunk→document), `next` (chunk→chunk), `mentions` (chunk→entity), `same_as {status: pending|confirmed|rejected, confidence}`, `superseded_by` (preference→preference).

Content-derived IDs make **every write idempotent** — re-extracting the same raw file cannot duplicate the graph.

---

## 6. Write Path (ingest → extract → gate)

### 6.1 `/kg:ingest` — get sources into `raw/`

Input: pdf | docx | md | url | text | (conversation via hook). Harness may fetch/convert when it has the tools; otherwise the skill calls `kg raw add`, whose converters (markitdown-class for pdf/docx/html, trafilatura for URLs) normalize to markdown.

```
1. Identify source type; check kg status / registry for prior ingestion (hash).
2. Convert to markdown with frontmatter {source, sha256, type, title, ingested_at,
   chunk boundaries}.
3. kg raw add … → file lands in raw/, registry line appended.
4. Write/refresh the one-line catalog entry in wiki/index.md.
5. Report: added, skipped-as-duplicate, suggest /kg:extract.
```

No extraction here. Ingest is cheap and safe to run in bulk.

**Chunking:** 512 tok / 64 overlap, tokenizer `tiktoken cl100k` (stable, no model dep). Boundaries computed once at `kg raw add`, written into frontmatter. Split on markdown headings first, then size — never mid-sentence.

### 6.2 `/kg:extract` — raw → POLE + facts + embeddings (harness intelligence)

The skill guides the harness LLM; only the final save touches the engine.

```
For each raw file:
1. Chunk per frontmatter boundaries.
2. Per chunk, prompt the harness model with:
   - the chunk text (ONE chunk, no IDs, no prior graph state — batchable)
   - ontology.json (the contract)
   - output schema: {nodes:[{type,subtype,name,summary,attributes}],
                     edges:[{source_name,semantic_type,target_name,summary}],
                     facts:[{subject,predicate,object}], preferences:[...]}
   Edge endpoints reference node `name`s WITHIN the same record.
3. Validate JSON against schema. On failure → retry with validator error
   appended (max 2 retries), then checkpoint the chunk as failed and continue.
4. kg save --nodes … --edges … --source raw/<f>#chunk-<n>
   → engine runs the normalization gate (§6.3), reports per-entity decision.
5. After the file: kg wiki sync --touched regenerates affected entity pages;
   mark the registry entry extracted; checkpoint progress (resumable).
6. Surface the gray-zone list ("2 pairs flagged — run /kg:dream or kg review list").
```

**Extraction is name-space-local.** Edges reference node `name` inside the same record only. The engine resolves names→IDs *after* the gate settles both endpoints. This makes chunks embarrassingly parallel and re-runnable.

**Checkpoint unit = chunk.** Registry line per raw file holds `chunks_done: [...]`, `chunks_failed: {n: reason}`. `/kg:extract` resumes from it. Failures never halt the file.

### 6.3 The Normalization Gate (inside `kg save`)

The only write path into the graph. Deterministic, per entity:

```
entity in
 ├─ 1. VALIDATE against ontology.json (reject → error to caller)
 ├─ 2. RESOLUTION — "what should we call this?" (names only, type-gated)
 │     alias-list hit → exact (normalized casing/whitespace)
 │     → fuzzy (RapidFuzz token-based, ≥0.85)
 │     → semantic (name-only light embedding, ≥0.80)
 │     short-circuit chain; match → canonical_name = matched, surface form
 │     appended to aliases; no match → canonical_name = extracted name.
 │     NO merges here.
 ├─ 3. EMBED — full-context vector (name+type+subtype+summary+high-signal attrs)
 ├─ 4. DEDUPLICATION — "is this the same real-world entity?"
 │     candidates = same-type nodes (ANN top-k ∪ same canonical_name)
 │     score = 0.7·cosine(full-context) + 0.3·fuzzy(full-context text)
 └─ 5. ROUTE
       score ≥ 0.95 → MERGE
       0.85–0.95    → NEW node + same_as{status:pending,confidence} edge → review queue
       < 0.85       → NEW node
```

Edges upserted after both endpoints settle; endpoint names re-map to post-merge IDs. Facts skip graph matching (islands) but are embedded + near-duplicate-checked against other facts.

**Merge semantics (explicit):** aliases ∪, sources ∪, attributes = existing wins + loser's value appended to `attribute_conflicts` for dream, summary = keep longer, re-embed, re-point loser's edges to winner, tombstone loser with `merged_into`. Pre-images of both logged to `wiki/log.md` — merge is the one unrecoverable move.

**Why resolution ≠ dedup is non-negotiable:** resolution absorbs "NYC"→"New York City" for grouping; dedup on richer signal keeps Paris-France ≠ Paris-Texas. Collapsing them rots graphs.

**Report contract:** `kg save` prints per-entity `NEW | RESOLVED-TO <id> | MERGED-INTO <id> (score) | FLAGGED <id> (score)` — machine-parseable, skill echoes it to user.

**Concurrency:** SQLite WAL, one writer. `kg save` takes a file lock; parallel extract processes queue on save. Cross-process blind spots are dream's RECENT-PAIR job.

---

## 7. Read Path (`/kg:query`)

- **Hybrid default.** FTS5/BM25 ∥ sqlite-vec ANN, both top-50, fused RRF k=60 → top-10 seeds. No reranker model.
- **Expand** = bidirectional BFS from seeds, `--hops 2`, recursive CTE. Hard subgraph cap (default 300 nodes) before packing — a hub node must not swallow the graph. Cap hit → keep highest-scoring frontier, log truncation.
- **Pack** ranks `0.5·RRF ∪ 0.3·degree-centrality ∪ 0.2·recency`, dedupes, trims to `pack_budget_tokens` (4000). Output is markdown (cheaper to read), each node carries `sources` so answers cite lineage.
- **NL→Cypher** mode: skill maps question to Cypher using `ontology.json`; `kg cypher` parses to AST and rejects any non-read clause (CREATE/MERGE/SET/DELETE/CALL). Read-only by construction.
- **Deep search** (exploratory / 50+ hits): `kg wiki build --from-query --hops 3` materializes `wiki/deep/<slug>/` with index + entity pages + cross-links. Skill answers by progressive disclosure: index → source page → derivatives → raw (last resort). Cache keyed by query slug + graph version; stale on version bump.
- **Read path writes back.** Every substantive answer appends a note to `wiki/notes/` + a line to `wiki/log.md`. Wiki grows from questions — continual learning on the read side.
- **Query never touches `raw/` by default.** Only `kg.db` is indexed; raw stays cold. Raw reads are explicit last-resort in deep search.

---

## 8. `/kg:dream` — Consolidation

Engine proposes, harness judges, engine executes. `kg dream candidates --since last-dream` emits a typed worklist; the model never auto-applies beyond threshold.

**Worklist kinds (each with the minimal evidence the model needs):**
- `RECENT-PAIR` — same-type nodes ingested since last dream, dedup-paired by ANN. DB-only (embeddings cached), cheap. Emit both summaries + score.
- `PENDING` — `same_as{status:pending}` edges awaiting judgment. Emit both nodes + original confidence + source overlap.
- `EXPIRING` — preferences/facts past `valid_until`, or open `superseded_by` chains. Emit the chain.
- `ORPHAN` — nodes whose every `sources` entry was deleted from raw/, or facts with no live source. Emit node + dangling refs.
- `CONTRADICT` — fact pairs sharing subject+predicate, conflicting object. Emit both + a one-line diff.
- `WIKI-LINT` — output of `kg wiki lint`: orphan pages, broken `[[links]]`, stale claims (page says X, graph says Y).

**Model actions map to exact commands:** `kg merge` · `kg review reject` · write `superseded_by` + set `valid_until` · `kg merge --tombstone-b` · keep-both (no-op, log) · escalate (leave in `review/same_as.md`).

**Hard rule:** auto-merge only at engine ≥0.95. The model may *confirm* gray-zone pairs (push over threshold) but never bulk-merge below it.

**New knowledge:** model may emit inferred facts/relations across sources during judgment — saved through the same `kg save` gate, tagged `source: dream/<date>`. The gate still applies, so dream output cannot pollute the graph.

**Close out:** `kg wiki sync && kg snapshot`, write dream report to `wiki/log.md` with counts per kind.

**Undo:** a merge is undone by re-extracting the source lineage. `raw/` makes it possible.

---

## 9. Visualization

`kg viz` serves localhost single port, reads `kg.db` directly (zero infra), works on a snapshot too. Force-directed graph (force-graph), nodes colored by POLE+O type, sized by degree, edge label = `semantic_type`. **Louvain community detection** in-engine over the node table — cluster view ↔ node view drill-down. Filters: type/subtype, time window, source doc, pending `same_as` overlay. Click node → side panel renders `wiki/entities/<slug>.md` (wiki and viz are two views of one graph). Obsidian remains the free fallback for `wiki/`; `kg export --cypher` is the power-user Neo4j path.

---

## 10. Portability

`kg install <harness>` writes, per the matrix:

| Harness | Skills delivery | Transport | Continual-learning hook |
|---|---|---|---|
| Claude Code | plugin: 4 skills + `kg` in PATH | shell (CLI) primary; MCP optional | SessionEnd hook → `kg raw add --type conversation` + incremental ~10 turns |
| Codex CLI | plugin/prompts port of same SKILL.md | shell primary; MCP | session-end wrapper script |
| OpenCode | instruction file + skills dir | shell; MCP | plugin event |
| Cursor | rules file summarizing skill contract | MCP primary (agent mode) | manual `/kg:ingest conversation` or MCP `ingest_conversation` |
| Anything else | generated `AGENTS.md` (inlined contract) | MCP stdio | manual |

Skills are markdown + a stable CLI contract → their only hard dependency is `kg` itself. Business logic never couples to one harness's keywords; swapping harness is a config pointer.

**Snapshot bootstrap:** `kg snapshot` writes `snapshots/kg.db.zst` (Zstandard). `kg init --from-snapshot` unpacks it so a teammate clones warm. Vectors re-embed lazily if the embedder differs (dim / `sha256(model)` mismatch in config).

---

## 11. CLI Commands (= MCP tools, same names, same core functions)

```
kg init [--backend sqlite] [--from-snapshot]
kg status                       # sources, node/edge counts, pending reviews, staleness

# ingest (called by /kg:ingest)
kg raw add <path|url|-> [--type ...] [--title ...]   # convert→md→raw/, register, dedupe by hash
kg raw list [--unextracted]

# save (called by /kg:extract) — the ONLY write path into the graph
kg save --nodes nodes.json --edges edges.json --source raw/<file>#chunk-<n>
        # runs the full normalization gate; prints per-entity decision report
kg resolve "<name>" --type person          # dry-run resolution
kg dedup-check <node.json>                 # dry-run dedup score

# query (called by /kg:query)
kg search "<query>" [--mode hybrid|semantic|bm25|keyword] [--type ...] [-k 10]
kg expand <node-id ...> [--hops 2] [--direction both] [--edge-types ...] [--json]
kg cypher "<read-only query>"
kg pack [-b 4000]   # reads subgraph JSON from stdin; -b overrides JSON budget_tokens
kg wiki build [--from-query "<q>" --hops 3]
kg wiki sync
kg wiki lint

# dream (called by /kg:dream)
kg dream candidates [--since last-dream]
kg merge <id-a> <id-b> [--tombstone-b]
kg review list|confirm|reject <same_as-id>

kg viz [--port 9749]
kg snapshot
kg export --cypher
kg install [claude-code|codex|opencode|cursor|all]
kg config get <key>
```

MCP exposes the agent-shaped subset (never raw DB ops): `ingest_url`, `ingest_file`, `ingest_text`, `ingest_conversation`, `save_pole`, `query_memory`, `nl_query_memory`, `deep_search_memory`, `dream_candidates`, `review_same_as` — plus MCP Resources for `wiki/index.md` and `ontology.json`.

---

## 12. Config (`.kg/config.toml`)

```toml
[project]
scope = "my-coding-agent"          # per-project memory; no cross-project bleed
user_id = "quan"

[backend]
kind = "sqlite"
path = ".kg/kg.db"

[embedding]
provider = "local"                 # local | voyage | openai | gemini
model = "bge-small-en-v1.5"        # 384d local default; voyage-3.5 @1024d if API
embed_fields = { person = ["name","summary","attributes.role","attributes.email"],
                 object = ["name","summary","attributes.model"] }

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
subgraph_cap = 300

[dream]
recent_window = "since-last-dream"
auto_hook = false                  # optional session-end mini-dream
```

Defaults baked into `kg init`; skills read via `kg config get <key>`.

---

## 13. Failure Modes & Safeguards

| Failure | Guard |
|---|---|
| Wrong merge (unrecoverable) | Gray zone never auto-merges; merges log both pre-images to `wiki/log.md`; lineage in `raw/` allows re-extraction to rebuild |
| Ontology drift / invented relation types | Extractor may only emit types present in `ontology.json`; `kg save` rejects everything else. Ontology changes are explicit, versioned edits → `kg reextract --affected` |
| NL query abuse | `kg cypher` parses to AST, rejects non-read clauses; NL mode read-only by construction |
| Parallel-ingest blind spots | Covered by dream's `RECENT-PAIR` sweep |
| Wiki rot | `kg wiki lint` in dream (orphans, broken links, stale claims, contradictions) |
| RAM / index bloat | Only `kg.db` is indexed; `raw/` stays cold |
| Interrupted extraction | Per-chunk checkpoints in registry; `/kg:extract` resumes |
| Hub node swallows graph | Subgraph cap before packing; truncation logged |

---

## 14. Skills (main flow)

Each skill = `SKILL.md` (workflow + when-to-trigger, "pushy" description) + `references/` (ontology contract, extraction prompt, few-shots, output schemas) + `scripts/` where deterministic. `SKILL.md` stays < 500 lines; progressive disclosure for the rest.

- **`/kg:ingest`** — get sources into `raw/`. Cheap, safe in bulk, no extraction.
- **`/kg:extract`** — raw → POLE + facts + embeddings via harness LLM, saved through the deterministic gate.
- **`/kg:query`** — read the memory: hybrid graph search, NL→Cypher, deep search; writes back a note per answer.
- **`/kg:dream`** — consolidate, update, clean: engine proposes a worklist, harness judges, engine executes.

---

## 15. Distribution & One-Line Install

The whole stack — build from source, install globally, and auto-configure one harness — is one command run from **this repository checkout**. There is no remote repo / hosted script in v1; the bootstrap builds the local tree. This command is also the entry point every integration test calls.

### 15.1 One-line bootstrap (from this directory)

```
make install            # default harness: claude code
make install H=cursor   # cursor
```

- `make install` → **Claude Code** (default).
- `make install H=cursor` → Cursor. (Codex/OpenCode accepted via `kg install`, not first-class-tested in v1.)
- `make install` expands to:
  ```
  uv sync && uv build && uv tool install ./dist/kg-*.whl && kg install ${H:-claude} && kg init
  ```
  i.e. resolve deps from the local tree → build the wheel from source → `uv tool install` (puts `kg` on PATH globally in an isolated env) → `kg install <harness>` (writes skills + MCP entry + instruction stanza + hooks into the target harness config) → `kg init` in cwd → prints a smoke result + the exact paths it wrote.

Prereqs: `uv` and the target harness installed. The Makefile detects a missing `uv` and prints the one-liner to install it.

### 15.2 Other `make` targets

`make build` (wheel only, no install), `make dev` (editable install + run from checkout — for working on `kg` itself), `make uninstall` (`uv tool uninstall kg` + `kg install --uninstall`), `make test`, `make bench`. All idempotent — re-running upgrades in place.

### 15.3 `kg install` contract (what `make install` delegates to)

`kg install [harness] [--all] [--uninstall]` writes, for the chosen harness: the 4 skills into its skills dir, an MCP stdio entry pointing at `kg mcp serve`, an instruction/`AGENTS.md` stanza, and the continual-learning hook (e.g. Claude Code `SessionEnd`). It must be **re-runnable and diff-friendly** — it merges into existing config rather than clobbering, and `--uninstall` reverses exactly what it wrote. This is what makes the bootstrap testable and reversible.

> **Future:** once a remote exists, a `curl … | bash` wrapper will simply `git clone` and run `make install` from the checkout — so nothing here assumes remote hosting, it just adds a fetch step in front.

---

## 16. Integration Tests

End-to-end tests that exercise the *real distribution path* and a *real harness session*, not unit fakes. **Claude Code and Cursor are first-class in v1;** Codex/OpenCode/`AGENTS.md` covered by the bootstrap command but their live-session tests land later.

### 16.1 Bootstrap test (all harnesses, v1)

Calls `make install H=<harness>` (from a clean checkout, in a throwaway temp project) and asserts `kg` is on PATH, `.kg/` is initialized, and `kg status` is green. This is the cheap gate that runs on every CI push and proves install works for every supported harness without spawning a session.

### 16.2 Live-session E2E test (Claude Code + Cursor, v1)

The serious test. For each of the two first-class harnesses:

1. **Bootstrap fresh** via `make install H=<harness>` in an isolated temp dir (a clean checkout of this repo).
2. **Seed** a small corpus (3 sources: one URL, one PDF, one pasted text) known to produce a measurable graph (N nodes, ≥1 cross-doc edge, ≥1 gray-zone pair).
3. **Spawn a new harness session** against that dir, headless:
   - Claude Code: `claude -p` (print/non-interactive) in a PTY, with the installed plugin + `kg` on PATH.
   - Cursor: the agent CLI / MCP client in non-interactive mode.
4. **Send a scripted message** that drives the full loop: ingest → extract → query → dream. e.g. *"ingest the 3 sources, extract them, then answer: who proposed RRF and what dedup threshold did we settle on?"*
5. **Monitor** the session end-to-end: stream output, watch `wiki/log.md` and registry for the expected decision report, with a timeout + failure-pattern grep covering crashes, hangs, empty graphs, and wrong-merge signatures.
6. **Assert** on the real artifacts, not the model's prose: node/edge counts in `kg.db` (within tolerance), the flagged pair reaches `same_as` pending, the answer cites lineage `sources`, `kg.db.zst` snapshot exists after dream.
7. **Determinism guard:** the corpus + expected graph shape are pinned (golden artifact). Model output is judged structurally, never by exact string — flaky text is the enemy of E2E.

A small harness library (`tests/e2e/`) wraps spawn/message/monitor so adding Codex/OpenCode later is one more driver, not a rewrite.

### 16.3 Portability E2E (the headline success criterion)

The test that proves criterion §1.1.1 (*harness swap works*): bootstrap with Claude Code, ingest+extract the corpus, snapshot; then bootstrap Cursor against the **same `.kg/`**, run the same query, assert the structural answer matches (same seed nodes, same lineage). Same memory, two harnesses, one truth.

---

## 17. Benchmarks (with vs without the stack)

Measures the four success criteria on a fixed benchmark corpus (growing scale: 10 / 50 / 250 documents, mixed PDF+URL+text, including intentional near-duplicates and a cross-doc inference target).

- **Answer quality vs baseline:** same questions asked (a) to a fresh harness with only the raw files on disk (the "grep" baseline) and (b) to a harness with `kg`. Scored on a rubric: cross-doc hits, multi-hop reach, lineage present. This is criterion §1.1.3 made measurable.
- **Graph cleanliness:** after full ingest+dream, count duplicate entities, invented edge types, wrong merges against a hand-labeled golden set. Target: zero invented types, merge precision/recall above thresholds. Criterion §1.1.2.
- **Cost + speed:** tokens consumed per extract, wall-clock for ingest+extract at each scale, p50/p95 query latency, `kg save` gate throughput. Criterion §1.1.4.
- **Portability cost:** overhead of swapping harness (bootstrap + first-query warmup) vs same-harness.

Results written to `bench/results/<date>/` as JSON + a markdown report; CI runs the small (10-doc) tier on every push, the large tier on release tags.

---

## 18. Documentation & Guidelines

- **`README.md`** — what it is, the one-line install, a 60-second walkthrough (the §16 end-to-end), link to the spec.
- **`docs/guides/`** — *Getting Started*, *Authoring Skills* (how to write a `/kg:*` skill against the CLI contract), *Per-Project Ontology Extension* (adding subtypes/semantic_types without breaking the gate), *Harness Setup* (one page per: Claude Code, Codex, OpenCode, Cursor, AGENTS.md).
- **`docs/architecture/`** — the three-plane model, the resolution-vs-dedup rationale (why the gate is non-negotiable), the storage adapter interface, the build plan.
- **`docs/ops/`** — *Recovery* (re-extract after a bad extraction / undo a merge via lineage), *Dream Tuning* (thresholds, when to escalate to human), *Team Bootstrap* (snapshot workflow, git policy).
- **Per-skill `references/`** — the ontology contract, extraction few-shots, output schemas (these *are* docs the harness reads at runtime).
- **`CHANGELOG.md`** + `ontology.json` versioning — every breaking ontology change is a version bump documented inline.

Guidelines live next to the code they govern; the spec stays the single source of truth for *why*, the guides for *how*.

---

## 19. Build Plan (milestones for the implementation plan)

1. **M0 — files only.** `kg init/raw add/status`, converters, registry, wiki index. A working LLM-wiki memory (no graph yet).
2. **M1 — graph + gate.** SQLite adapter (nodes/edges/FTS5/sqlite-vec), `kg save` with the full normalization gate, `kg search/expand/pack`, `/kg:extract` + `/kg:query` skills.
3. **M2 — dream + review.** `kg dream candidates`, merge/review commands, `/kg:dream` skill, snapshot.
4. **M3 — serving breadth.** FastMCP wrapper, `kg install` for the four harnesses, conversation hooks.
5. **M4 — polish.** `kg viz` (+Louvain), Cypher-subset reader, `--cascade` extraction, deep-search wiki caching policies.
6. **M5 — distribution + E2E.** One-line bootstrap (`install.sh` + `make install`), `kg install --uninstall` reversibility, the bootstrap CI test for all harnesses, the live-session E2E harness (Claude Code + Cursor) and the portability E2E.
7. **M6 — benchmarks + docs.** The with/without benchmark suite (small tier in CI, large on release), and the full documentation set (README, guides, architecture, ops).

**Build-vs-buy checkpoint:** if M1's gate feels heavy, the escape hatch is to keep the skills + CLI contract but back `kg save`/`kg search` with Graphiti or neo4j-labs/agent-memory. The interface plane is designed so the knowledge plane's backend is swappable without touching a single skill.

---

## 20. End-to-End Walkthrough

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

*Everything the harness learns flows back in; nothing you know lives in the harness.*
