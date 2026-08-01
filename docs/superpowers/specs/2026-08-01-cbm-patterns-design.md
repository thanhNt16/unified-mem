# CBM Pattern Adoption Design

**Date:** 2026-08-01
**Status:** Draft — approval required before implementation
**Goal:** Apply relevant codebase-memory-mcp patterns to kg without creating competing source-of-truth graphs or replacing SQLite.

## Scope

Four independent deliverables, sequenced by dependency:

| Phase | Deliverable | Why first |
|---|---|---|
| P0 | Filesystem identity registry + correct chunk checkpoints | Makes all later indexing incremental/correct |
| P1a | Staged rebuild + atomic publish | Makes bulk extraction crash-safe and replaceable |
| P1b | Structural code projection (Python + TypeScript) | Deterministic code truth without LLM duplication |
| P2 | Index modes + coverage report | Exposes cost/coverage tradeoffs to users |

## Non-negotiable Authority Boundaries

- Raw files + Git own evidence.
- Existing `registry.jsonl` global SHA remains normalized-content dedupe.
- New `file_hashes` owns filesystem identity/cache only; it does not replace content dedupe.
- Tree-sitter owns structural code facts (symbols, containment, imports/calls where parseable).
- LLM owns document semantics, POLE entities, claims, preferences; it never recreates deterministic code edges.
- kg.db stays derived/rebuildable. No second graph database.

## P0: Filesystem Identity Registry + Checkpoints

### Data

Add `.kg/file_hashes.jsonl`, atomically written:

```json
{
  "project": "scope",
  "rel_path": "src/auth.py",
  "sha256": "...",
  "mtime_ns": 0,
  "size": 0,
  "kind": "code|document",
  "extractor_version": "...",
  "chunk_count": 4,
  "chunks_done": [0, 1],
  "chunks_failed": {"2": "schema error"},
  "output_node_ids": ["..."],
  "status": "pending|partial|extracted|stale|deleted"
}
```

### Rules

1. Same `(project, rel_path, sha256, extractor_version)` → skip extraction.
2. Changed hash → mark prior outputs stale; reprocess all source chunks.
3. Missing previously-seen path → mark deleted; candidates enter tombstone/review path, never silent hard-delete.
4. Source only becomes `extracted` when every chunk is done or explicitly failed; first chunk cannot mark whole source extracted.
5. Existing `registry.jsonl` SHA identity continues de-duplicating identical normalized source content across paths.

### CLI

```bash
kg index status
kg index changed
kg index scan <path>
```

## P1a: Staged Rebuild + Atomic Publish

### Lifecycle

```text
raw/ + registry.jsonl + file_hashes.jsonl
  → .kg/.rebuild-<uuid>/kg.db
  → integrity check + counts + coverage validation
  → fsync
  → atomic replace .kg/kg.db
```

- Staging occurs inside `.kg/` (same filesystem); never `/tmp`.
- Atomic `mkdir(.writer.lock)` prevents parallel writer/rebuild. Lock includes PID + hostname; no automatic stealing.
- Initial/full rebuild creates empty staging DB.
- Incremental rebuild clones published DB then applies changed sources only when changed files ≤10%; full rebuild otherwise.
- POSIX rename preserves old readers; Windows requires local process quiesce before replacement.
- Crash leaves live DB intact or a recoverable validated staging candidate. Recovery never deletes live DB first.

### CLI

```bash
kg index rebuild [--full]
kg index recover
kg index status
```

## P1b: Structural Code Projection

### MVP

- Languages: Python, TypeScript/JavaScript.
- Parser: Tree-sitter, pinned grammar versions.
- Code nodes use existing `object` type with subtypes: `code_file`, `function`, `class`, `module`.
- Structural relationships use existing vocabulary where possible: `part_of`, `uses`, `mentions`; parser metadata stores exact relation (`imports`, `calls`, `defines`) under attributes until ontology expansion is separately approved.
- IDs: `code:{project}:{rel_path}#{qualified_name}`.
- Provenance: source path, SHA-256, byte/line span, parser+grammar version, projection generation.

### Boundaries

- Python/TypeScript function/class/module/file containment first.
- Imports/calls only when parser resolution is unambiguous; unresolved symbols are recorded as coverage gaps, not inferred edges.
- LSP, Go, Rust, 158-language support, semantic similarity, and full call hierarchy are deferred.

### CLI

```bash
kg index code <path> [--mode fast|moderate|full]
kg index status
```

## P2: Index Modes + Coverage

| Mode | Does | Skips |
|---|---|---|
| `fast` | hashes, source inventory, Tree-sitter structural projection | embeddings, LLM semantics, similarity edges |
| `moderate` | fast + local embeddings + document extraction | expensive global similarity/community enrichment |
| `full` | moderate + configured semantic similarity/community jobs | nothing configured |

Default: `moderate` for existing document flows. `fast` becomes default for code-only indexing.

Coverage artifact `.kg/coverage.json` reports:

```json
{
  "generation": 0,
  "mode": "moderate",
  "sources": {"seen": 0, "indexed": 0, "skipped": 0, "failed": 0, "stale": 0},
  "by_reason": {"unchanged": 0, "unsupported": 0, "parse_error": 0},
  "outputs": {"nodes": 0, "edges": 0},
  "started_at": "...",
  "completed_at": "..."
}
```

`kg index status` prints compact counts and failure reasons; JSON supports CI.

## Interfaces and Files

| Area | New/Changed |
|---|---|
| Registry | `src/kg/file_hashes.py` (new), `registry.py` bridge |
| Index orchestration | `src/kg/index.py` (new), `src/kg/cli/index_cli.py` (new) |
| Rebuild | `src/kg/rebuild.py` (new), `paths.py`, `snapshot.py` reuse |
| Code projection | `src/kg/code_index.py` (new) |
| Config | `config.py` index section + mode/thresholds |
| Coverage | `src/kg/coverage.py` (new) |
| CLI | `cli/main.py` register `index` commands |
| Tests | registry, checkpoint, rebuild recovery, Python/TS fixtures, modes/coverage, E2E |

## Acceptance Gates

| Gate | Target |
|---|---|
| Unchanged scan | zero parser/LLM/embed calls; status=skipped |
| Changed source | only changed source reprojected; old outputs stale/reconciled |
| Chunk correctness | partial source remains partial after first chunk |
| Crash safety | injected failure never corrupts published kg.db |
| Atomic publish | readers get old or new DB, never partial DB |
| Structural MVP | Python + TS fixture extracts files/functions/classes/containment deterministically |
| Fast mode | no embedding calls, no LLM calls |
| Coverage | every discovered source reports indexed/skipped/failed/stale with reason |
| Regression | all current tests pass |

## Final Evaluation Benchmark + Rollback Gate

Run after every CBM track is integrated, before release or commit:

```bash
uv run pytest tests/ -x
PYTHONPATH=. uv run kg bench --scale 10 --dimension all
PYTHONPATH=. uv run pytest tests/test_scale_10k.py -v -s
PYTHONPATH=. uv run python bench/scale_100k.py
```

### Baseline Floors

| Metric | Floor | Current baseline |
|---|---:|---:|
| Test suite | 0 failures | 766 passed, 3 skipped |
| Recall@5 | ≥0.90 | 1.00 |
| Recall@10 | ≥0.95 | 1.00 |
| MRR | ≥0.85 | 0.96 |
| 100k build (100k nodes / 150k edges) | ≤10s | 3.4s |
| 100k hybrid search | ≤50ms | 2.4ms |
| 100k graph-aware find | ≤200ms | 33ms |
| 100k `/graph.json` hot | ≤500ms | 131ms |
| 10k full scale suite | 5/5 pass | 5/5 pass |

### Regression Protocol

1. Run the full evaluation after each track, retaining machine-readable outputs under `bench/results/<timestamp>/`.
2. If a floor fails, compare the changed track against the prior artifact; isolate with its targeted tests and benchmark.
3. Fix the regression, then rerun the whole evaluation. Do not mask it by raising a threshold.
4. If the regression remains after one targeted fix cycle, revert only that track's commits/files to the last green benchmarked state. Preserve raw sources, registry, and generated benchmark artifacts.
5. A final implementation is accepted only when every floor passes; otherwise the affected CBM improvement is deferred.

## Non-goals

- No CBM import or second persistent graph.
- No 158-language parser/LSP clone.
- No automatic LLM reinterpretation of code structure.
- No distributed locks/network filesystem support.
- No automatic lock stealing.
- No DB replacement.
