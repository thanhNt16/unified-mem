# Quickstart

From a fresh repo checkout to first memory save and search in under two minutes.

## 1. Install

```bash
git clone <repo> && cd unified-mem
make install                  # build + global tool install + kg install + kg init
```

`make install` runs the full bootstrap: builds the wheel, installs `kg` as a global
tool via `uv tool install`, applies the default Claude Code harness integration
(`kg install claude --apply`), and initializes `.kg/` in the current project.

Hack on kg itself instead of installing:

```bash
make dev                      # editable install
```

## 2. Initialize a project

If you skipped `make install`, initialize the per-project memory:

```bash
kg init --user-id $USER --scope my-project
```

This creates `.kg/` with `config.toml`, `ontology.json`, `registry.jsonl`, `raw/`,
`wiki/index.md`, and an empty `kg.db` (SQLite, WAL mode). To bootstrap warm from a
teammate's committed snapshot:

```bash
kg init --user-id $USER --scope my-project --from-snapshot snapshots/kg.db.zst
```

## 3. Add a source

```bash
echo "Demis Hassabis founded DeepMind in 2010." | \
  kg raw add - --type text --title hassabis-note
kg raw list
kg status
```

`kg raw add` normalizes any source (text, markdown, html, pdf, docx, url) to an
immutable markdown mirror in `.kg/raw/`. Identical content re-added is a no-op
(sha256-checked). Extraction is a separate step — see
[the architecture overview](../architecture/overview.md).

## 4. Extract and save

The extract skill (delivered by `kg install`) drives the harness LLM through
chunked extraction and calls the engine's only write path:

```bash
kg save --nodes nodes.json --edges edges.json \
  --source raw/2026-07-26--text--hassabis-note.md#chunk-0
```

`kg save` runs the [normalization gate](../architecture/overview.md#the-normalization-gate)
(validate → resolve → embed → dedup → route) and prints a per-entity decision
report (`NEW`, `RESOLVED-TO <id>`, `MERGED-INTO <id>`, or `FLAGGED <id>`).

## 5. Search

```bash
kg search "who founded DeepMind"
kg expand <node-id> --hops 2 --json
```

Search is hybrid by default: FTS5 (BM25) parallel sqlite-vec ANN, fused with
Reciprocal Rank Fusion (k=60). `kg expand` walks the graph from seed nodes via a
recursive CTE.

## 6. Snapshot for a teammate

```bash
kg snapshot                       # writes .kg/snapshots/kg.db.zst
git add .kg/raw .kg/wiki .kg/registry.jsonl .kg/snapshots/kg.db.zst
git commit -m "share warm memory"
```

A teammate clones, then `kg init --from-snapshot snapshots/kg.db.zst` boots warm.

## What to read next

- [Harness setup](harness-setup.md) — install kg into Claude Code, Codex, OpenCode, Cursor, or `AGENTS.md`.
- [Architecture overview](../architecture/overview.md) — the layered design and why resolution is not dedup.
- [Runbook](../ops/runbook.md) — snapshot, dream, wiki sync, common issues.
