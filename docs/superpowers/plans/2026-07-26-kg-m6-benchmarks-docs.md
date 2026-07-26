# KG M6 — Benchmarks & Documentation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:subagent-driven-development or superpowers:executing-plans. Checkbox (`- [ ]`) tracking.

**Goal:** Prove the four success criteria with measurements (spec §17), and ship the full documentation set (spec §18): README, guides, architecture rationale, ops runbooks, and per-skill references. The benchmark suite compares kg against a grep baseline and reports graph-cleanliness, cost, and speed at 10/50/250-doc scales.

**Architecture:** A `bench/` Python package with corpus generators, a baseline runner (raw files only, no kg), a kg runner, a rubric scorer, and a JSON+markdown reporter. CI runs the 10-doc tier per push; the 250-doc tier runs on release tags. Docs live next to the code they govern; the spec remains the source of truth for *why*.

**Tech Stack:** pytest-based bench runner (`pytest-benchmark` optional); the E2E harness from M5 for the live answer-quality rubric; plain markdown for docs.

> **Dependency:** benchmarks need M1–M5 (a real graph, real query, real install). Some metrics (answer-quality vs grep) require the live harness; gate those behind the same binary-presence skip as M5. Cost/token metrics require reading the harness's token-usage output — wire to whatever the M5 driver already exposes.

## Global Constraints

- **No fabricated numbers** (spec §17, reinforced in the M0 report): the reporter writes only measured values; missing measurements are `null` with a reason, never invented.
- **Small tier in CI, large on release tags** (spec §17): 10-doc per push; 50/250 nightly or on tag.
- **Rubric, not string-match** for answer quality (mirrors M5 §16.2 determinism guard).
- **Docs next to code** (spec §18): per-skill `references/` are runtime docs; guides are human docs.

---

## File Map

| File | Responsibility |
|---|---|
| `bench/__init__.py`, `corpus.py` | generate/seed 10/50/250-doc corpora (mixed pdf+url+text, near-dups, cross-doc target) |
| `bench/runners.py` | `run_baseline(project)`, `run_kg(project)` — drive ingest→extract→query on both |
| `bench/metrics.py` | `graph_cleanliness(adapter, golden)`, `cost_from_log()`, `timed()` context manager |
| `bench/rubric.py` | `score_answer(answer, expected_facts) -> {cross_doc, multihop, lineage}` |
| `bench/report.py` | `write_report(results, out_dir) -> Path` (JSON + markdown) |
| `bench/results/<date>/` | gitignored outputs (tracked as a sample only) |
| `tests/bench/test_metrics.py`, `test_rubric.py` | unit tests for the measurers |
| `docs/guides/*`, `docs/architecture/*`, `docs/ops/*`, `CHANGELOG.md` | the doc set |

---

### Task 1: Corpus generator

**Files:** `bench/corpus.py`, `tests/bench/test_corpus.py`
**Interfaces — Produces:** `make_corpus(out_dir, scale)` where scale ∈ {10,50,250}; seeds mixed sources with 2 intentional near-duplicates and 1 cross-doc inference target per spec §17. Deterministic (fixed seed, no `random` at module import).

- [ ] **Step 1: Failing test** — `make_corpus(tmp, 10)` yields 10 files, ≥2 share a sha-prefix-different near-dup, ≥2 mention the same person.
- [ ] **Step 2–5]** TDD. Commit `feat(m6): benchmark corpus generator`.

---

### Task 2: Metrics — cleanliness, cost, speed

**Files:** `bench/metrics.py`, `tests/bench/test_metrics.py`
**Interfaces — Produces:** `graph_cleanliness(adapter, golden) -> {duplicate_entities, invented_edge_types, wrong_merges, merge_precision, merge_recall}`; `timed() -> (seconds)` context manager; `cost_from_log(paths) -> {tokens, usd}|None` (parses harness token output if present, else None).

- [ ] **Step 1: Failing test** — cleanliness on a graph with a known duplicate and an invented edge returns nonzero counts.
- [ ] **Step 2–5]** TDD. Commit `feat(m6): benchmark metrics (cleanliness/cost/speed)`.

---

### Task 3: Answer-quality rubric

**Files:** `bench/rubric.py`, `tests/bench/test_rubric.py`
**Interfaces — Produces:** `score_answer(answer: str, expected: dict) -> dict` scoring cross-doc hits, multi-hop reach, lineage presence against `expected_facts`.

- [ ] **Step 1: Failing test** — an answer citing two sources and a 2-hop fact scores higher than a single-source grep-style answer.
- [ ] **Step 2–5]** TDD. Commit `feat(m6): answer-quality rubric`.

---

### Task 4: Baseline vs kg runners + reporter

**Files:** `bench/runners.py`, `report.py`, `tests/bench/test_report.py`
**Interfaces — Produces:** `run_baseline(project, queries)` answers using raw files only (the grep baseline). `run_kg(project, queries)` answers via `kg search/expand`. `write_report(results, out_dir)` emits `results.json` + `report.md` with the spec §17 table filled from real runs (or `null` + reason).

- [ ] **Step 1: Failing test** — `write_report` with synthetic results writes valid JSON + markdown containing every metric key.
- [ ] **Step 2–5]** TDD; run the small tier for real once M5 is present to populate a real sample. Commit `feat(m6): baseline/kg runners + reporter`.

---

### Task 5: Bench CLI + CI tiers

**Files:** `bench/__main__.py` (or `kg bench`), `.github/workflows/bench.yml`; Modify `Makefile` (`make bench`)
**Interfaces — Produces:** `python -m bench --scale 10|50|250 --out bench/results/<date>` runs the suite; CI: 10-doc per push, 50/250 on tag.

- [ ] **Step 1: Failing test** — `python -m bench --scale 10` exits 0 and writes a report dir.
- [ ] **Step 2–5]** TDD; wire CI. Commit `feat(m6): bench CLI + CI tiers`.

---

### Task 6: Documentation set

**Files:** `README.md` (expand), `docs/guides/{getting-started,authoring-skills,ontology-extension,harness-setup-*}.md`, `docs/architecture/{three-plane,resolution-vs-dedup,storage-interface}.md`, `docs/ops/{recovery,dream-tuning,team-bootstrap}.md`, `CHANGELOG.md`
- [ ] **Step 1:** Outline + draft each guide against the now-realized M0–M5 behavior (no speculative features).
- [ ] **Step 2:** `docs/architecture/resolution-vs-dedup.md` is the keystone — explain why the gate is non-negotiable with the Paris-France/Paris-Texas example.
- [ ] **Step 3:** Add a doc-test `tests/test_docs_links.py` asserting every internal markdown link resolves and every guide mentions a real command.
- [ ] **Step 4: Run PASS**
- [ ] **Step 5: Commit** — `feat(m6): full documentation set + CHANGELOG`

---

### Task 7: Regenerate the status report + M6 acceptance

**Files:** `docs/reports/<date>-m6-status-report.html` (refresh of the M0 report)
- [ ] **Step 1:** Re-run the report generator with all milestones LIVE and real benchmark numbers populated from a small-tier run; every former PLANNED badge flips to DONE; the benchmark table has measured values.
- [ ] **Step 2:** Acceptance test `tests/test_acceptance_m6.py` asserts the report HTML contains no `PLANNED`/`NOT MEASURED` tokens for in-scope items, and that `bench/results/<latest>/report.json` has non-null values for the 10-doc tier.
- [ ] **Step 3: Commit** — `feat(m6): refresh status report with measured benchmarks; M6 complete`

---

## Self-Review

**Spec coverage (§17, §18):** answer-quality vs baseline (§17/§1.1.3) → Tasks 3,4. graph cleanliness (§17/§1.1.2) → Task 2. cost + speed (§17/§1.1.4) → Tasks 2,4. portability overhead (§17) → reuses M5 portability E2E timing. small-tier CI / large on release (§17) → Task 5. README + guides + architecture + ops + per-skill refs + CHANGELOG (§18) → Task 6. ✓ Gaps: per-skill `references/` are authored in M1–M4 with their skills; M6 only verifies/links them.

**Placeholder scan:** none. Real measurements only; `null`+reason is explicit, not a TODO.

**Type consistency:** `graph_cleanliness(adapter, golden)` shape matches the reporter; `score_answer` returns the keys the report table consumes. ✓

---

## Execution Handoff

**Plan saved to `docs/superpowers/plans/2026-07-26-kg-m6-benchmarks-docs.md`.** This completes the full plan set (M0 executed, M1–M6 planned).
