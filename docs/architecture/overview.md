# Architecture overview

Four layers, one memory.

```
           Harness (Claude Code, Codex, OpenCode, Cursor, AGENTS.md)
             │  skills  │  MCP (stdio)  │  hooks
             ▼
           CLI / MCP server          ← thin, same core functions
             ▼
           Core engine              ← all deterministic logic lives here
             │  gate  │  search  │  traverse  │  pack  │  cypher
             ▼
           Storage adapter          ← interface; SQLite is v1
             │  nodes/edges  │  FTS5  │  sqlite-vec  │  WAL
             ▼
           .kg/ directory           ← local-first, git-friendly
             raw/  wiki/  registry.jsonl  ontology.json  config.toml  kg.db
```

CLI and MCP call the same core functions. Neither owns business logic — they are
two transport surfaces. This is the one invariant that keeps harnesses in sync.

## Layers

### Files layer (M0)

`kg raw add` normalizes any source (text, md, html, pdf, docx, url, conversation)
to immutable markdown with YAML frontmatter in `.kg/raw/`. Re-adding identical
content is a no-op (sha256-checked via `registry.jsonl`). Chunk boundaries are
precomputed at ingest time and stored in frontmatter, making extraction
deterministic and resumable.

### Graph layer (M1)

SQLite tables `nodes` and `edges` + recursive-CTE traversal + FTS5 (BM25) +
`sqlite-vec` (approximate nearest-neighbor). The [data model](data-model.md)
defines POLE+O node types, content-derived IDs, and the ontology contract.

### MCP layer (M3)

`kg mcp serve --project-root <path>` exposes the agent-shaped tool subset
(ingest, save, query, dream, review) as JSON-RPC over stdio. Read-only by
default; `--allow-writes` enables write tools. MCP Resources expose
`wiki/index.md` and `ontology.json`.

### Install layer (M5)

`kg install <harness>` writes the per-harness integration (skills, hooks, rules,
MCP config) so the harness LLM drives the same `.kg/`. A committed manifest
makes install and uninstall exact and reversible. See [harness setup](../guides/harness-setup.md).

## Content-derived IDs

Every node ID is `{user_id}:{type}:{slug(canonical_name)}`. Every edge ID is
`{source_id}|{semantic_type}|{target_id}`. Both are derived from content, not
generated — re-extracting the same raw file produces the same IDs and makes every
write idempotent.

Slug: lowercase, non-alnum → `-`, collapse, trim, 80-char cap. Collision on same
type+slug is intentional — that *is* the resolution hit. A rejected dedup pair
gets a `-2` (etc.) suffix stored explicitly so IDs stay stable.

## Resolution is not dedup (non-negotiable)

The normalization gate has two separate steps, and collapsing them rots graphs.

**Resolution** answers "what should we call this?" It absorbs surface-form
variations — `NYC` → `New York City`, `Demis Hassabis, CEO` → `Demis Hassabis`.
It uses name-only signals (alias list, fuzzy token match ≥ 0.85, light name
embedding ≥ 0.80) and runs within a single type. It appends the surface form to
aliases; it does **not** merge.

**Dedup** answers "is this the same real-world entity?" It runs on full-context
embeddings + fuzzy text match over same-type candidates. Score = 0.7·cosine +
0.3·fuzzy. Results:

| Score | Action |
|---|---|
| ≥ 0.95 | Auto-merge |
| 0.85–0.95 | New node + `same_as{status:pending}` edge → review queue |
| < 0.85 | New node |

**The Paris example.** Resolution maps `Paris, France` and `Paris, Texas` to two
different canonical names — correctly, because their aliases differ. Dedup then
scores them on full-context embeddings (country, coordinates, population,
notable features). A score of ~0.4 lands firmly in the "new node" bucket. If you
had skipped dedup and let resolution alone decide, `Paris` would match `Paris`
and collapse two cities into one — the graph-rot scenario the gate exists to
prevent.

## Gray zone

The 0.85–0.95 range is the **gray zone**: entities that look similar but aren't
certainly identical. Instead of auto-merging, the gate creates a `same_as` edge
with `status: pending` and the dedup score. These appear in `kg review list`
and `kg dream candidates --kind PENDING` for human (or harness LLM) judgment.
`kg review confirm` pushes the pair over threshold; `kg review reject` dismisses
it. This is the only place where judgment lives — the engine is otherwise fully
deterministic.

## Tombstone

When a merge happens, the loser node gets `status: tombstoned` and a
`merged_into` pointer to the winner. The loser's edges are re-pointed to the
winner. Tombstones are resolvable by ID — queries for the old ID still find
the entity. Merge pre-images are logged to `wiki/log.md` because merge is the
one unrecoverable move. Undo requires re-extracting from `raw/`.

## `kg.db` is derived state

The database is rebuildable from `raw/` via the extract skill. `raw/` +
`registry.jsonl` are the source of truth; the DB is the materialized view. Bad
extraction → delete affected nodes by lineage, re-extract the raw file. Git
commits `raw/ wiki/ registry.jsonl ontology.json config.toml snapshots/` and
ignores `kg.db`.

## Deferred features

- **Deep Cypher tools** (write Cypher, advanced graph algorithms) — planned for M4+.
- **NL→Cypher** translation is realized as a skill-side concern, not an engine command.
- **`kg export --cypher`** — not yet implemented.
- **Auto-dream hook** (`dream.auto_hook` in config) — config key exists; runtime hook not wired.

## What to read next

- [Data model](data-model.md) — Node/Edge schema, ontology contract, storage adapter interface.
- [Runbook](../ops/runbook.md) — snapshot, dream, wiki sync, recovery.
