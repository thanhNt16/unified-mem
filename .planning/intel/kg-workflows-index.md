# /kg:* Workflow Index

Four core workflows that compose the unified memory layer:

## Workflows

| Workflow | Purpose | Evidence Boundary |
|---|---|---|
| [/kg:ingest](./kg-ingest-workflow.md) | Evidence capture + routing | Raw sources cold, provenance preserved |
| [/kg:extract](./kg-extract-workflow.md) | Raw → typed knowledge | Per-chunk isolation, gate normalization |
| [/kg:query](./kg-query-workflow.md) | Questions → answers | Read-only, source-cited |
| [/kg:dream](./kg-dream-workflow.md) | Graph maintenance | Human-verdict, no auto-merge |

## Data Flow

```
ingest → extract → (kg.db) → query
                         ↓
                       dream → (kg.db)
```

## Existing Skills

Three skills already exist at `/Users/harrynguyen/.claude/skills/kg-*`:

- **kg-extract** (`SKILL.md`) — see `.claude/skills/kg-extract/`
- **kg-query** (`SKILL.md`) — see `.claude/skills/kg-query/`
- **kg-dream** (`SKILL.md`) — see `.claude/skills/kg-dream/`

## Missing Skills/Commands

### Skills (need creation)

- **kg-ingest** — Missing entirely (only empty directory at `.claude/skills/kg-ingest/`)
  - Should live at `/Users/harrynguyen/.claude/skills/kg-ingest/SKILL.md`
  - Content: Evidence preservation, format normalization, routing to specialized engines

### Commands (need implementation)

| Command | Status | Used By |
|---|---|---|
| `kg ingest` | Missing | /kg:ingest |
| `kg raw status` | Deferred to M2 | /kg:extract |
| `kg raw checkpoint` | Deferred to M2 | /kg:extract |
| `kg raw rechunk` | Deferred to M2 | /kg:ingest |
| `kg cypher` | Deferred to M2 | /kg:query (NL-Cypher mode) |
| `kg wiki build` | Deferred to M4 | /kg:query (deep-search) |
| `kg dream status` | Missing | /kg:dream |
| `kg dream prune` | Missing | /kg:dream |
| `kg query` | Missing (use kg search+expand+pack) | /kg:query (router) |

### Commands (already implemented)

- `kg save` — Gate normalization (src/kg/cli/save.py)
- `kg search`, `kg expand`, `kg pack` — Query primitives (src/kg/cli/query.py)
- `kg dream candidates` — Queue inspection (src/kg/cli/dream.py)
- `kg review confirm`, `kg review reject` — Verdict execution (src/kg/cli/review.py)
- `kg merge` — Manual merge (src/kg/cli/review.py)
- `kg snapshot` — Recovery point (src/kg/cli/snapshot_cmd.py)
- `kg wiki sync` — Wiki regeneration (src/kg/cli/wiki_cli.py)
- `kg init` — Initialize .kg/ (src/kg/cli/init.py)
- `kg status` — Show project state (src/kg/cli/status.py)

## Integration Matrix

| Workflow | codebase-memory-mcp | Graphify | AgentMemory | wiki |
|---|---|---|---|---|
| **ingest** | Route code sources | Route transcripts | Route agent-memory | Create stub pages |
| **extract** | Extract code entities | Extract conversation nodes | Extract observations | Regenerate entity pages |
| **query** | Query code structure | Query conversation history | Query session memory | Materialize deep views |
| **dream** | Update code refs | Re-index mentions | Delete observations | Rebuild affected pages |

## Cross-Workflow State

| State | Location | Updated By | Read By |
|---|---|---|---|
| Source registry | `.kg/registry/index.jsonl` | ingest | extract |
| Chunk boundaries | `raw/<file>.md` frontmatter | ingest | extract |
| Chunk state | registry (chunks_done/failed) | extract | extract (resume) |
| Pending edges | `kg.db` | extract (gate) | dream |
| Embeddings | `kg.db` | extract | query |
| Wiki pages | `wiki/entities/` | extract, dream | query |
| Audit log | `wiki/log.md` | dream | (immutable) |
| Snapshots | `.kg/snapshots/` | extract, dream | recovery |

## Deferred to Later Milestones

- **M2:** `kg ingest`, `kg cypher`, `kg raw rechunk`, `kg raw status`, `kg raw checkpoint`
- **M4:** `kg wiki build --from-query` (deep-search materialization)
