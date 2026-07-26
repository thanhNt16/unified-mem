# KG M4 — Visualization, Cypher Reader & Deep Search Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:subagent-driven-development or superpowers:executing-plans. Checkbox (`- [ ]`) tracking.

**Goal:** Add the polish layer: `kg viz` (local force-directed graph UI + Louvain community detection), `kg cypher` (read-only Cypher subset → SQLite translator, AST-validated), `kg wiki build --from-query` (deep-search materialization) + `kg wiki lint`, and the `--cascade` extraction pre-filter. These are independent features sharing only the M1 adapter.

**Architecture:** Four independent sub-systems over the existing `SQLiteAdapter`. viz is a stdlib `http.server` serving a single HTML page that reads a JSON graph endpoint; Louvain runs in-engine over the node/edge tables. The Cypher reader parses a small subset (MATCH/WHERE/RETURN) to an AST, rejects write clauses by construction, and lowers to the adapter's `neighbors`/`fts_search`. Deep search composes `hybrid_search` + `expand` + writes a scoped wiki.

**Tech Stack:** stdlib `http.server` + inline JS (force-graph via CDN or a tiny self-hosted SVG renderer); pure-Python Louvain (or `networkx` community); a hand-written recursive-descent Cypher-subset parser; builds on M1/M2/M3.

> **Interface assumption:** reads `SQLiteAdapter.conn` for raw graph export (viz) and uses `neighbors`/`fts_search`/`vec_search`. If M1 added a public `export_graph()` helper, prefer it.

## Global Constraints

- **Cypher is read-only by construction** (spec §13): parser rejects CREATE/MERGE/SET/DELETE/REMOVE/CALL/DROP at the AST level, not by string filter.
- **viz reads kg.db directly, zero infra** (spec §9): no external DB, works on a snapshot too. Single localhost port.
- **Deep-search wiki cache keyed by query slug + graph version** (spec §7): stale on version bump.
- **No new core writes.** viz/cypher/deep-search are read paths; cascade is an extract-side pre-filter only.

---

## File Map

| File | Responsibility |
|---|---|
| `src/kg/cypher/__init__.py`, `lexer.py`, `parser.py`, `translator.py` | Cypher subset → AST → SQL |
| `src/kg/community.py` | Louvain over nodes/edges → cluster labels |
| `src/kg/viz/__init__.py`, `server.py`, `page.py` | localhost UI + JSON graph endpoint |
| `src/kg/deepsearch.py` | `build_deep_wiki(adapter, embedder, query, hops, paths)` |
| `src/kg/wiki_lint.py` | `lint(paths, adapter) -> list[Issue]` (orphans, broken links, stale claims) |
| `src/kg/cascade.py` | `pre_filter(markdown, embedder) -> list[span]` (spaCy/GLiNER optional) |
| CLI: `src/kg/cli/{viz,cypher_cli,wiki_cli(extend),cascade}.py` |
| tests per module |

---

### Task 1: Cypher lexer + parser (read-only subset)

**Files:** `src/kg/cypher/lexer.py`, `parser.py`, `__init__.py`; `tests/cypher/test_parser.py`
**Interfaces — Produces:** `parse(query: str) -> AST` where AST nodes are `Match`, `Where`, `Return`. Raises `CypherError` (subclass of `KgError`) on write clauses or unsupported syntax.

- [ ] **Step 1: Failing test**

```python
# tests/cypher/test_parser.py
import pytest
from kg.cypher import parse, CypherError


def test_parse_match_return():
    ast = parse("MATCH (n:person) RETURN n")
    assert ast.return_items
    assert ast.match.labels == {"person"}


def test_rejects_write_clause():
    for q in ["CREATE (n)", "MERGE (n)", "DELETE n", "SET n.x=1", "DROP INDEX i"]:
        with pytest.raises(CypherError):
            parse(q)
```

- [ ] **Step 2: Run FAIL**
- [ ] **Step 3: Implement** — tokenizer (idents, punctuation, labels `:person`, keywords uppercased) + recursive-descent parser. Accept `MATCH (n[:Label])-[r:TYPE]*]->(m) WHERE <pred> RETURN ...`. On encountering any write keyword token, raise `CypherError("read-only: <kw> not allowed")`.
- [ ] **Step 4: Run PASS**
- [ ] **Step 5: Commit** — `feat(m4): Cypher-subset lexer + parser (read-only by construction)`

---

### Task 2: Cypher translator → adapter calls

**Files:** `src/kg/cypher/translator.py`, `tests/cypher/test_translator.py`
**Interfaces — Produces:** `translate(ast, adapter) -> list[dict]` lowering MATCH on `:Label` + WHERE name-contain into `fts_search`/`neighbors`; returns rows of `{node_id, name, type, summary}`.

- [ ] **Step 1: Failing test** — seed graph, `translate(parse("MATCH (n:person) WHERE n.name =~ 'Demis' RETURN n"), adapter)` returns the Demis row.
- [ ] **Step 2–5]** TDD cycle. Commit `feat(m4): Cypher → adapter translator`.

---

### Task 3: Louvain community detection

**Files:** `src/kg/community.py`, `tests/test_community.py`; Modify `pyproject.toml` (`networkx>=3` optional)
**Interfaces — Produces:** `louvain(adapter) -> dict[node_id, int]` cluster labels.

- [ ] **Step 1: Failing test** — two obvious cliques share no edges → 2 clusters.
- [ ] **Step 2–5]** TDD. Use `networkx.community.louvain_communities` over an in-memory graph built from `edges`. Commit `feat(m4): Louvain community detection`.

---

### Task 4: `kg viz` server

**Files:** `src/kg/viz/server.py`, `page.py`, `__init__.py`; `tests/viz/test_server.py`; CLI `src/kg/cli/viz.py`
**Interfaces — Produces:** `serve(adapter, port) -> None` runs `http.server` with routes: `/` → HTML page, `/graph.json` → `{nodes:[{id,name,type,degree,cluster}], edges:[{source,target,semantic_type}]}`. `kg viz [--port 9749]`.

- [ ] **Step 1: Failing test** — start server on a thread, `GET /graph.json`, assert JSON has the seeded nodes.
- [ ] **Step 2–5]** TDD. Page is a single HTML string with inline JS (force-graph via CDN `<script>`). Node color by POLE type, size by degree, cluster grouping from Task 3. Commit `feat(m4): kg viz local graph UI + /graph.json`.

---

### Task 5: Deep search → scoped wiki

**Files:** `src/kg/deepsearch.py`, `tests/test_deepsearch.py`; CLI extend `kg wiki build`
**Interfaces — Produces:** `build_deep_wiki(adapter, embedder, query, hops, paths, config) -> Path` — hybrid_search → expand → write `wiki/deep/<slug>/` with index.md + entity pages + cross-links. Cache version = node count + max updated_at.

- [ ] **Step 1: Failing test** — after save, `build_deep_wiki(...)` writes `wiki/deep/<slug>/index.md` referencing the matched entity.
- [ ] **Step 2–5]** TDD. Commit `feat(m4): deep-search wiki materialization`.

---

### Task 6: `kg wiki lint`

**Files:** `src/kg/wiki_lint.py`, `tests/test_wiki_lint.py`; CLI `kg wiki lint`
**Interfaces — Produces:** `lint(paths, adapter) -> list[Issue(kind, path, detail)]` — orphan entity pages (no matching active node), broken `[[links]]`, stale claims (page summary ≠ node summary). Feeds dream's WIKI-LINT kind.

- [ ] **Step 1: Failing test** — entity page for a tombstoned node → ORPHAN issue.
- [ ] **Step 2–5]** TDD. Commit `feat(m4): kg wiki lint`.

---

### Task 7: `--cascade` extraction pre-filter

**Files:** `src/kg/cascade.py`, `tests/test_cascade.py`
**Interfaces — Produces:** `pre_filter(markdown, config) -> list[(start,end,type)]` returning candidate entity spans. v1: spaCy NER if available, else a regex proper-noun heuristic; never blocks extraction, only prioritizes.

- [ ] **Step 1: Failing test** — heuristic mode finds a Capitalized sequence.
- [ ] **Step 2–5]** TDD. Commit `feat(m4): cascade extraction pre-filter`.

---

### Task 8: M4 acceptance

**Files:** `tests/test_acceptance_m4.py`
- [ ] **Step 1:** seed graph; `kg cypher "MATCH (n:person) RETURN n"` returns Demis; `kg viz` `/graph.json` responds; `kg wiki build --from-query "DeepMind"` writes a deep wiki; `kg wiki lint` runs clean on a fresh sync.
- [ ] **Step 2:** full suite green.
- [ ] **Step 3: Commit** — `feat(m4): acceptance test; M4 complete`

---

## Self-Review

**Spec coverage (§7 deep-search, §9 viz, §11 cypher/wiki/lint, §13):** viz + Louvain (§9) → Tasks 3,4. Cypher read-only (§11/§13) → Tasks 1,2. Deep search (§7) → Task 5. `kg wiki lint` (§8 WIKI-LINT, §13) → Task 6. Cascade (§6.2 cost note) → Task 7. ✓ Gaps: Neo4j `kg export --cypher` (§9 power-user path) — defer to a follow-up; not on the v1 critical path.

**Placeholder scan:** Tasks 2–7 use the compact TDD-cycle shorthand (Steps 2–5) because the pattern is established in M1; each names its concrete failing-test assertion and commit message. No TODO/TBD.

**Type consistency:** `parse()->AST` (Task 1) consumed by `translate` (Task 2); `louvain(adapter)->dict` (Task 3) consumed by viz (Task 4); `lint()` Issue shape feeds dream WIKI-LINT. ✓

---

## Execution Handoff

**Plan saved to `docs/superpowers/plans/2026-07-26-kg-m4-viz-cypher.md`.** Next: **M5 (distribution + E2E)**, then **M6 (benchmarks + docs)**.
