# kg

Portable, local-first unified memory layer — **files + vectors + graph** — for any
harness (Claude Code, Codex, OpenCode, Cursor). The harness LLM does extraction
and judgment; the `kg` engine does deterministic storage, matching, and search.

> **Status:** M0–M6 + graph-aware query shipped — files + vectors + graph + MCP
> + installers + portability E2E + benchmarks + graph-aware hybrid search
> (Recall@10 = 1.00) + 220× scale fix to 100k nodes. 795 passed, 3 skipped.
> Realized commands: `init`, `raw add/list`, `status`, `config`, `save`,
> `search`, `expand`, `pack`, `resolve`, `dedup-check`, `cypher`,
> `wiki sync/build/lint`, `dream candidates`, `merge`, `review`, `snapshot`,
> `query`, `bench`, `viz`, `install`, `mcp serve`, `hook session-end`.
> See [the current status report](docs/reports/2026-08-01-status.md),
> [the benchmark guide](bench/BENCHMARKS.md), the
> [architecture overview](docs/architecture/overview.md), and the
> [quickstart](docs/guides/quickstart.md). Full design in
> `docs/superpowers/specs/2026-07-26-kg-unified-memory-design.md`.

## Documentation

- [Quickstart](docs/guides/quickstart.md) — `make install`, `kg init`, first save, first search.
- [Harness setup](docs/guides/harness-setup.md) — install kg into Claude Code, Codex, OpenCode, Cursor, or `AGENTS.md`.
- [Architecture overview](docs/architecture/overview.md) — layered design, content-derived IDs, resolution vs dedup, gray zone, tombstone.
- [Data model](docs/architecture/data-model.md) — Node/Edge schema, ontology contract, storage adapter.
- [Runbook](docs/ops/runbook.md) — snapshot/restore, dream/review, wiki sync.
- [Troubleshooting](docs/ops/troubleshooting.md) — drift errors, MCP failures, missing skills.
- Spec: `docs/superpowers/specs/2026-07-26-kg-unified-memory-design.md` (full design).
- [Current status](docs/reports/2026-08-01-status.md) — shipped features, measured performance, remaining work.
- [Benchmarks](bench/BENCHMARKS.md) — reproducible benchmark commands, dimensions, current results.
- [3D graph demo](https://thanhnt16.github.io/unified-mem/) — real repo graph + 10k-node stress dataset.
- [CHANGELOG.md](CHANGELOG.md) — milestone-by-milestone changes.

## Quick start (from this checkout)

```bash
make dev                 # editable install for hacking on kg itself
kg init --user-id $USER --scope my-project
echo "some fact" | kg raw add - --type text --title note
kg raw list
kg status
```

You now have a working, git-friendly LLM-wiki memory under `.kg/`:

```
.kg/
  config.toml        backend, embedder, thresholds, chunking (spec §12)
  ontology.json      the contract — node/edge types (spec §5)
  registry.jsonl     one line per source, sha256-deduped
  raw/               immutable markdown mirrors of every source (+ frontmatter)
  wiki/index.md      the catalog the agent reads first
  conversations/     session transcripts (from the SessionEnd hook, M3)
```

`raw/` files are immutable and write-once. Re-adding identical content is a
no-op (registry-checked by sha256). Chunk boundaries are precomputed into each
file's frontmatter so extraction (M1) is deterministic and resumable.

## Build / install / uninstall

```bash
make build             # wheel into dist/
make test              # run the suite
make install           # full bootstrap (M5): build + global tool install + kg install + kg init
make install H=cursor  # ...targeting Cursor instead of the default (claude)
make uninstall
```

`make install` requires `uv` (and the target harness). M5 wires the
`kg install <harness>` auto-configuration step; for M0 only the files layer is
present.

## Sources supported by `kg raw add`

| Type | How | Converter |
|---|---|---|
| `text` | stdin (`-`) or literal | passthrough |
| `md` / `markdown` | path | passthrough |
| `html` | path | markitdown |
| `pdf` / `docx` | path | markitdown |
| `url` | `http(s)://…` | trafilatura |
| `conversation` | transcript body | passthrough (→ `raw/conversations/`) |

Type is auto-detected from the path suffix / scheme when `--type` is omitted.
When `--title` is omitted, the first H1 heading of the document is used.

## Development

```bash
uv sync --group dev      # install + dev deps
uv run pytest            # 41 tests, ~1s
uv run pytest -k chunking
```

Python is pinned to 3.13 (`.python-version`) — `onnxruntime`, needed for local
embeddings in M1, has no cp314 wheels yet.

## Design sources

`kg` consolidates ideas from *Agent Memory From Scratch*, *Keep Your Knowledge
Graph Clean*, the *Context Layer* portability doctrine, LLM-wiki patterns, and
the `codebase-memory-mcp` engineering shape. See spec §0 for the full
source-consolidation table.
