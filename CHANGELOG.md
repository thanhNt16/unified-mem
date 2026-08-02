# Changelog

Per-milestone changes to kg. Realized commands only; deferred features are
flagged in [the architecture overview](docs/architecture/overview.md).

## [Unreleased]

- **Updated** the shared pinned CBM 3D graph UI for live `kg viz` and static GitHub Pages, using deterministic Python layout and matching live/static geometry. Retired the duplicate `docs/demo/` frontend; the wheel now bundles the same frontend assets for runtime use.

## [M6b] — 2026-08-01 — Graph-Aware Query, Scale Fixes, 3D Demo, CBM Patterns

- **Added** `kg query "<q>" [--intent find|trace|explain]` — graph-aware hybrid
  search. Intent classifier (LLM + heuristic fallback) selects graph-stream
  depth; `diversify_by_source` + `adaptive_budget` pack context; note writer +
  token capture. Recall@5/10 = 1.00, MRR 0.96 (16.7× semantic recall vs grep).
- **Added** recall benchmark (`bench/recall.py`) + comparative baseline dimension
  wired into `bench/runners.py`/`reporter.py`.
- **Fixed** scale to 100k nodes: batched transactions + `executemany` (build
  749s→3.4s, **220×**), FTS bulk-skip-delete (O(N²)→O(N)), indexed dedup,
  materialized community cache (`/graph.json` 9.6s→131ms hot, **73×**), SQL viz
  aggregation, budget-aware traversal with recursive `LIMIT`.
- **Added** CBM-inspired indexing patterns: content-addressed
  `FileHashRegistry` (`src/kg/file_hashes.py`) skip-unchanged extraction, and
  `IndexPlan`/`plan_index` (`src/kg/index.py`) staged rebuild planning.
- **Added** 3D graph visualization demo (`docs/demo/`, deployed to GitHub Pages)
  adapted from [codebase-memory-mcp](https://github.com/DeusData/codebase-memory-mcp)
  (MIT, DeusData). Two datasets: real repo (1,774 nodes / 1,598 edges) and
  synthetic stress (10,000 nodes / 33,917 edges). Neighbor-aware hover
  highlight, click-to-detail panel.
- **Added** `kg viz [--port 9749]` materialized community cache (generation
  counter + `node_clusters` table).
- **Docs** consolidated: current status report, benchmark guide rewrite, stale
  completed plans/research proposals removed. 795 passed, 4 skipped (2 Pi-installer tests paused — incomplete harness work).

## [M6] — 2026-07-27 — Benchmarks & Docs

- **Added** benchmark suite (`bench/`): deterministic corpus generator (10/50/250-doc scales, seeded), graph-cleanliness metrics, keyword-grounded answer-quality rubric, baseline (grep) vs kg runners, honest reporter. No fabricated numbers — only measured values; unrun tiers marked NOT_MEASURED.
- **Added** `kg bench --scale N` CLI (10 = CI-safe default) + `make bench` target (sets PYTHONPATH so the dev-only `bench` harness resolves from the repo checkout).
- **Added** `.github/workflows/bench.yml` — deterministic 10-doc bench on push + weekly; no API key/LLM/network.
- **Added** documentation set: `docs/guides/{quickstart,harness-setup}.md`, `docs/architecture/{overview,data-model}.md`, `docs/ops/{runbook,troubleshooting}.md`. `tests/test_docs.py` guards against stale-command drift (every documented command verified against realized `main.py`).
- **Added** status report `docs/reports/2026-07-27-m6-status-report.html` — M0–M6 milestones COMPLETE, commands LIVE, 10-doc benchmarks MEASURED (cleanliness 0.994, median 3.9ms, rubric 1.0), 50/250 NOT_MEASURED (release-gated), cost null (driver emits no usage tokens).
- **Honesty**: cost metrics null with documented reason; large-scale tiers not fabricated; rubric is keyword string-match (not an LLM judge), renamed accordingly.

## [M5] — 2026-07-26 — Distribution & E2E

- **Added** harness installer matrix: `claude`, `codex`, `opencode`, `cursor`,
  `agents`, `all`. `kg install <harness> --apply` writes owned fragments and a
  committed manifest; `--uninstall` is exact and reversible; drift raises an
  actionable error.
- **Added** `kg mcp serve --project-root <path> [--allow-writes]` — JSON-RPC over
  stdio, read-only by default.
- **Added** `kg hook session-end --project-root <path> [--session-root <path>]`
  — Claude Code conversation-ingest hook.
- **Added** E2E harness library + portability E2E (cross-harness structural
  equality), reversibility E2E across all 5 harnesses, Cursor MCP driver E2E,
  Claude Code live-session driver E2E, golden corpus + deterministic extracted
  fixture, M5 acceptance test.
- **Added** CI workflow that gates on the deterministic tier; live-session tier
  lives in `.github/workflows/test.yml`.
- **Changed** `make install` applies harness install with `--skills-src`.

## [M4] — 2026-07-26 — Visualization & Cypher

- **Added** `kg cypher "<query>" [--json]` — read-only Cypher subset → SQL
  translator. Non-read clauses (CREATE/MERGE/SET/DELETE/CALL) rejected by the
  parser, not silently dropped.
- **Added** `kg viz [--port 9749] [--wiki <path>]` — localhost graph UI with
  Louvain community detection.
- **Added** `kg wiki build from-query "<q>" [--hops N]` — materializes
  `wiki/deep/<slug>/` for exploratory queries.
- **Added** `kg wiki lint` — orphans, broken `[[wikilinks]]`, stale summaries.
- **Fixed** Cypher translator to reject `WITH` clause at translate time rather
  than silently dropping it.
- **Fixed** wiki lint stale-summary false-positive via escaped compare.

## [M3] — 2026-07-26 — MCP & Install

- **Added** MCP server (`kg.mcp.server`) exposing the agent-shaped tool subset:
  `ingest_url`, `ingest_file`, `ingest_text`, `ingest_conversation`, `save_pole`,
  `query_memory`, `nl_query_memory`, `deep_search_memory`, `dream_candidates`,
  `review_same_as`. MCP Resources for `wiki/index.md` and `ontology.json`.
- **Added** harness installer scaffolding (per-harness plan/apply/uninstall
  dispatch, manifest, fingerprinting, drift detection).
- **Added** `agents` harness — generated `AGENTS.md` fallback for the rest.

## [M2] — 2026-07-26 — Dream & Review

- **Added** `kg dream candidates [--since <ts>] [--kind <k>] [--json]` —
  consolidation worklist. Kinds: `RECENT-PAIR`, `PENDING`, `EXPIRING`, `ORPHAN`,
  `CONTRADICT`, `WIKI-LINT`.
- **Added** `kg merge <winner-id> <loser-id> [--reason <text>]` — explicit merge
  with tombstone + `merged_into` pointer. Pre-images logged to `wiki/log.md`.
- **Added** `kg review list | confirm <edge-id> --winner <id> [--reason] |
  reject <edge-id> [--reason]` — same_as judgment surface.
- **Added** `kg snapshot [--output|-o <path>]` — Zstandard-compressed snapshot
  of `kg.db` for team bootstrap.
- **Added** `kg init --from-snapshot <path> [--force]` — warm bootstrap from a
  committed snapshot.

## [M1] — 2026-07-26 — Graph Gate

- **Added** `kg save --nodes <path> --edges <path> --source <str>
  [--chunks <path>] [--facts <path>] [--preferences <path>] [--cascade]
  [--cascade-budget N]` — the only write path into the graph.
- **Added** normalization gate: validate → resolve → embed → dedup → route.
  Routes: `NEW`, `RESOLVED-TO`, `MERGED-INTO` (≥0.95), `FLAGGED` (0.85–0.95).
- **Added** `kg resolve "<name>" --type <type>` and `kg dedup-check <node.json>`
  dry-run surfaces.
- **Added** content-derived node/edge IDs — every write is idempotent.
- **Added** SQLite adapter: `nodes`/`edges` tables, recursive-CTE neighbors,
  FTS5 (BM25), `sqlite-vec` (ANN).
- **Added** `kg search "<q>" [--mode hybrid|semantic|bm25|keyword] [--type T]
  [-k N]`, `kg expand <id...> [--hops N] [--json]`, `kg pack [-b N]` (stdin JSON).
- **Added** StorageAdapter interface (`upsert_*`, `get`, `delete`, `neighbors`,
  `fts_search`, `vec_search`, `cypher_read`) — v1 ships SQLite; other backends
  are a future PR.

## [M0] — 2026-07-26 — Files Layer

- **Added** `kg init [--user-id <id>] [--scope <s>]` — creates `.kg/` with
  `config.toml`, `ontology.json`, `registry.jsonl`, `raw/`, `wiki/index.md`,
  empty `kg.db`.
- **Added** `kg raw add <path|url|-> [--type T] [--title T] [--conversation]`
  — normalizes any source to immutable markdown with YAML frontmatter in
  `.kg/raw/`. Idempotent via sha256.
- **Added** `kg raw list [--unextracted]`, `kg status`, `kg config get <key>`.
- **Added** chunking (512 tok / 64 overlap, `tiktoken cl100k`) with boundaries
  written into frontmatter for deterministic re-extraction.

## Notes on deferred features

- **`kg export --cypher`** — not yet implemented. Realized in narrative as a
  future power-user path.
- **Auto-dream session hook** (`dream.auto_hook` in `config.toml`) — config key
  present, runtime wiring deferred.
- **Deep Cypher write operations** — `kg cypher` is read-only by construction.
  Write Cypher / advanced graph algorithms are a future milestone.
