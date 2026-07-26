# KG M5 — Distribution & End-to-End Tests Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:subagent-driven-development or superpowers:executing-plans. Checkbox (`- [ ]`) tracking.

**Goal:** Make the one-line bootstrap real and provable: the `make install` flow builds + globally installs + runs `kg install`, and a test harness proves (a) bootstrap works for every supported harness, and (b) a real live Claude Code *and* Cursor session, spawned headless against a fresh `.kg/`, drives ingest→extract→query→dream and produces the expected graph — and that the same `.kg/` answers identically across both harnesses (the portability claim).

**Architecture:** A small E2E harness library (`tests/e2e/`) abstracts "spawn a harness session, send a message, stream output, assert on artifacts." Each harness is a *driver* behind a common interface so adding Codex/OpenCode later is one driver, not a rewrite. A pinned golden corpus (3 sources with known graph shape) anchors structural assertions; model prose is never string-matched.

**Tech Stack:** `pty`/`subprocess` for headless session spawn; `claude -p` (Claude Code print mode) and the Cursor agent CLI/MCP client; pytest. The bootstrap test shells out to `make install`.

> **Honest uncertainty:** the exact non-interactive invocation of each harness (`claude -p` flags today, Cursor's headless agent entrypoint) will need verification against the installed versions at execution time. The plan fixes the *contract* and *assertions*; the driver's spawn command is the part most likely to need a one-line tweak. Mark drivers `@pytest.mark.skipif` when a harness binary is absent so CI stays green on machines without it.

## Global Constraints

- **Bootstrap test runs on every push** (spec §16.1); live-session E2E is gated on the harness binary being present (skip otherwise) so a dev box without Cursor doesn't fail CI.
- **Assert on artifacts, not prose** (spec §16.2): node/edge counts in `kg.db`, `same_as` pending presence, lineage `sources`, snapshot file existence — never exact model text.
- **Golden corpus is pinned** under `tests/e2e/fixtures/corpus/`; expected graph shape in `golden.json`. Structural matching with tolerances.
- **One entry point for install** (spec §15): `make install` is what the bootstrap test calls — no parallel install path.

---

## File Map

| File | Responsibility |
|---|---|
| `tests/e2e/__init__.py`, `harness.py` | `SessionDriver` ABC + spawn/message/monitor/timeout helpers |
| `tests/e2e/drivers/claude_code.py` | `claude -p` PTY driver |
| `tests/e2e/drivers/cursor.py` | Cursor agent driver |
| `tests/e2e/fixtures/corpus/` | 3 pinned sources (url.md, paper.pdf, note.md) |
| `tests/e2e/fixtures/golden.json` | expected graph shape (node count band, required edges, gray-zone pair) |
| `tests/e2e/assertions.py` | `assert_graph_shape(adapter, golden)`, `assert_lineage(answer_text, sources)` |
| `tests/e2e/test_bootstrap.py` | `make install H=<harness>` green for all harnesses |
| `tests/e2e/test_live_claude.py`, `test_live_cursor.py` | live-session E2E |
| `tests/e2e/test_portability.py` | same `.kg/`, two harnesses, one answer |
| `Makefile` (modify) | ensure `install` target is the documented one-liner |

---

### Task 1: E2E harness library + assertions

**Files:** `tests/e2e/harness.py`, `assertions.py`, `__init__.py`, `drivers/__init__.py`
**Interfaces — Produces:** `SessionDriver` ABC: `start(project_dir)`, `send(message) -> str`, `wait_for(predicate, timeout)`, `stop()`. `assert_graph_shape(adapter, golden)` checks node-count within `[min,max]`, required edges present, ≥1 gray-zone. `corpus_path()` + `load_golden()`.

- [ ] **Step 1: Failing test** — unit-test `assert_graph_shape` against an in-memory adapter seeded from golden (pass) and a depleted one (fail). This pins the assertion logic before any session spawn.
- [ ] **Step 2–5]** TDD. Commit `feat(m5): e2e harness library + graph-shape assertions`.

---

### Task 2: Golden corpus + expected shape

**Files:** `tests/e2e/fixtures/corpus/{note.md,paper.pdf,url.md}`, `golden.json`
- [ ] **Step 1:** Author 3 sources that deliberately produce: ≥8 nodes, ≥1 cross-doc edge (a person mentioned in two sources), and 1 intentional gray-zone pair (two near-duplicate entity names). Hand-extract the expected nodes/edges into `golden.json` (the *reference* extraction a correct run must approximate).
- [ ] **Step 2:** Add a test `test_golden_is_well_formed` validating golden.json schema.
- [ ] **Step 3: Commit** — `feat(m5): golden corpus + expected graph shape`

---

### Task 3: Bootstrap test (all harnesses)

**Files:** `tests/e2e/test_bootstrap.py`
**Interfaces — Produces:** parametrized test over `[claude-code, codex, opencode, cursor, agents-md]` that, in a tmp home + project, runs `make install H=<harness>`, asserts `kg` on PATH, `.kg/` initialized, `kg status` exits 0. Skips a harness if `kg install <harness>` writer is absent (not the binary — the installer; installers exist from M3).

- [ ] **Step 1: Failing test** — for `claude-code` only first.
- [ ] **Step 2–5]** TDD, then parametrize. Commit `feat(m5): bootstrap CI test for all harnesses`.

---

### Task 4: Claude Code live-session driver

**Files:** `tests/e2e/drivers/claude_code.py`, `tests/e2e/test_live_claude.py`
**Interfaces — Produces:** `ClaudeCodeDriver` spawning `claude -p` in a PTY with the project cwd + installed plugin. `send(msg)` writes + reads until idle; `wait_for` polls `wiki/log.md` / registry. The live test: bootstrap → seed corpus → send the scripted ingest→extract→query→dream message → assert graph shape + lineage + snapshot. `@pytest.mark.skipif(not which('claude'))`.

- [ ] **Step 1: Failing test** (skipped without `claude`).
- [ ] **Step 2–5]** Implement driver; run locally with Claude present to validate the spawn flags; commit. Commit `feat(m5): Claude Code live-session E2E driver`.

> If `claude -p`'s non-interactive contract differs (e.g. needs `--output-format stream-json`, a session resume flag, or a plugin-load flag), fix the driver's spawn line — that's the expected adjustment point.

---

### Task 5: Cursor live-session driver

**Files:** `tests/e2e/drivers/cursor.py`, `tests/e2e/test_live_cursor.py`
- [ ] **Step 1–5]** Mirror Task 4 for Cursor's headless agent entrypoint (MCP client against the installed `kg` MCP server, or the Cursor CLI). Skip if absent. Commit `feat(m5): Cursor live-session E2E driver`.

---

### Task 6: Portability E2E (headline success criterion)

**Files:** `tests/e2e/test_portability.py`
- [ ] **Step 1: Failing test** (skipped unless both `claude` + cursor present): bootstrap Claude Code, ingest+extract corpus, snapshot; bootstrap Cursor against the *same* `.kg/`, run the same query, `assert_graph_shape` matches and seed-node lineage is identical. This is spec §16.3 / criterion §1.1.1.
- [ ] **Step 2–5]** TDD. Commit `feat(m5): portability E2E — same .kg, two harnesses`.

---

### Task 7: `kg install --uninstall` reversibility test

**Files:** `tests/e2e/test_reversible.py`
- [ ] **Step 1: Failing test** — `make install H=claude-code` then `kg install --uninstall claude-code`; assert `.claude.json` MCP entry gone, skills removed, hook removed, `CLAUDE.md` stanza stripped, manifest cleared.
- [ ] **Step 2–5]** TDD. Commit `feat(m5): install --uninstall reversibility test`.

---

### Task 8: M5 acceptance + CI wiring

**Files:** `.github/workflows/test.yml` (or equivalent), `tests/e2e/test_acceptance_m5.py`
- [ ] **Step 1:** CI runs bootstrap test (all harnesses) on every push; live-session + portability tests are `allow-failure: false` but only execute on a runner with the harness binaries (matrix/label-gated).
- [ ] **Step 2:** acceptance test orchestrates bootstrap → live Claude → portability in one flow (skipped gracefully if binaries absent).
- [ ] **Step 3: Commit** — `feat(m5): CI wiring + acceptance; M5 complete`

---

## Self-Review

**Spec coverage (§15, §16):** one-line bootstrap via `make install` (§15) → Tasks 3,7. Bootstrap CI test all harnesses (§16.1) → Task 3. Live-session E2E Claude + Cursor (§16.2) → Tasks 4,5. Portability E2E (§16.3) → Task 6. Reversibility (§15.3) → Task 7. ✓ Gaps: Codex/OpenCode live drivers deferred (bootstrap-only coverage in v1, per spec §16) — explicit.

**Placeholder scan:** spawn-flag uncertainty is flagged inline as the expected adjustment point, not hidden. All assertions are concrete.

**Type consistency:** `SessionDriver` ABC (Task 1) implemented by both drivers (Tasks 4,5); `assert_graph_shape(adapter, golden)` used by live + portability tests; `load_golden()` returns the same shape everywhere. ✓

---

## Execution Handoff

**Plan saved to `docs/superpowers/plans/2026-07-26-kg-m5-distribution-e2e.md`.** Next (final): **M6 (benchmarks + docs)**.
