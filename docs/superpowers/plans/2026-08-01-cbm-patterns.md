# CBM Pattern Adoption Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add CBM-inspired incremental source identity, staged rebuild, deterministic Python/TypeScript structural projection, index modes, and coverage reporting without adding a second graph or database.

**Architecture:** Preserve `raw/` + existing `registry.jsonl` as source truth and global normalized-content dedupe. Add a separate atomic filesystem identity cache, a staged SQLite rebuild publisher, and a code projection that emits existing `object` nodes plus structural edges with parser provenance. The CLI exposes one `kg index` namespace, with modes determining work and coverage always reporting results.

**Tech Stack:** Python 3.13, Typer, Pydantic, SQLite WAL/FTS5/sqlite-vec, Tree-sitter Python/TypeScript grammars, pytest.

## Global Constraints

- Keep SQLite primary; do not add a second persistent graph or DB.
- Existing `registry.jsonl` SHA semantics remain global normalized-content dedupe.
- `file_hashes.jsonl` is a path-level incremental cache, never an authority source.
- Code parser owns only deterministic structural facts; LLM document extraction stays separate.
- Staging occurs under `.kg/` on the same filesystem; never use `/tmp` for publish candidates.
- Never delete or overwrite live `kg.db` before a candidate validates.
- Default code scope: Python + TypeScript/JavaScript only.
- Run full regression evaluation after integration. If a floor fails, isolate/fix once; otherwise revert the affected track.

---

## File Structure

| File | Responsibility |
|---|---|
| `src/kg/file_hashes.py` | Path-level hash records, atomic JSONL persistence, changed/deleted detection |
| `src/kg/coverage.py` | Indexed/skipped/failed/stale counters, coverage JSON artifact |
| `src/kg/index.py` | Index orchestration, mode policy, source discovery, status model |
| `src/kg/rebuild.py` | Writer lock, staged DB lifecycle, validation, atomic publication, recovery |
| `src/kg/code_index.py` | Tree-sitter source projection for Python/TS/JS |
| `src/kg/cli/index_cli.py` | `kg index` Typer commands |
| `src/kg/config.py` | IndexConfig defaults and TOML render/parse |
| `src/kg/paths.py` | New file_hashes/coverage/lock/staging path helpers |
| `src/kg/cli/main.py` | Register index commands |
| `tests/test_file_hashes.py` | Identity/cache/change detection tests |
| `tests/test_coverage.py` | Coverage artifact tests |
| `tests/test_rebuild.py` | Stage/publish/recovery/lock tests |
| `tests/test_code_index.py` | Python/TS fixture projection tests |
| `tests/test_index_cli.py` | CLI modes/status/coverage E2E |
| `tests/fixtures/code_index/` | Tiny deterministic Python + TS fixtures |

## Task 1: Path-Level File Hash Registry

**Files:**
- Create: `src/kg/file_hashes.py`
- Modify: `src/kg/paths.py`
- Test: `tests/test_file_hashes.py`

**Interfaces:**
- Produces `FileHashRecord`, `FileHashRegistry`, `scan_paths()`.
- `FileHashRegistry.load(path) -> FileHashRegistry`
- `FileHashRegistry.classify(project, root) -> FileChanges`
- `FileHashRegistry.write_atomic(path) -> None`

- [ ] **Step 1: Write failing tests**

```python
def test_classify_reports_new_unchanged_changed_and_deleted(tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    (root / "a.py").write_text("x = 1\n")
    registry = FileHashRegistry()
    first = registry.classify("p", root)
    assert [item.rel_path for item in first.new] == ["a.py"]
    registry.apply(first, extractor_version="v1")
    registry.write_atomic(tmp_path / "file_hashes.jsonl")

    assert FileHashRegistry.load(tmp_path / "file_hashes.jsonl").classify("p", root).unchanged[0].rel_path == "a.py"
    (root / "a.py").write_text("x = 2\n")
    changed = registry.classify("p", root)
    assert [item.rel_path for item in changed.changed] == ["a.py"]
    (root / "a.py").unlink()
    assert [item.rel_path for item in registry.classify("p", root).deleted] == ["a.py"]
```

- [ ] **Step 2: Run failure check**

Run: `uv run pytest tests/test_file_hashes.py -v`
Expected: FAIL, `ModuleNotFoundError: No module named 'kg.file_hashes'`.

- [ ] **Step 3: Implement records and atomic persistence**

```python
@dataclass(frozen=True)
class FileHashRecord:
    project: str
    rel_path: str
    sha256: str
    mtime_ns: int
    size: int
    kind: str
    extractor_version: str
    chunk_count: int = 0
    chunks_done: tuple[int, ...] = ()
    chunks_failed: dict[str, str] = field(default_factory=dict)
    output_node_ids: tuple[str, ...] = ()
    status: str = "pending"
```

Use `Path.read_bytes()` + `hashlib.sha256`; write newline-delimited sorted JSON to `<target>.tmp`, `flush`, `os.fsync`, `os.replace`, then fsync parent directory on POSIX. Add `KgPaths.file_hashes()` returning `.kg/file_hashes.jsonl`.

- [ ] **Step 4: Implement classification**

`classify()` recursively discovers regular files below root, ignores `.git`, `.kg`, `__pycache__`, compares `(sha256, mtime_ns, size)` by `(project, rel_path)`, returns `new`, `unchanged`, `changed`, `deleted`. New/changed output is sorted by relative POSIX path.

- [ ] **Step 5: Run tests**

Run: `uv run pytest tests/test_file_hashes.py -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/kg/file_hashes.py src/kg/paths.py tests/test_file_hashes.py
git commit -m "feat: add path-level file hash registry"
```

## Task 2: Correct Chunk Checkpoints + Coverage Artifact

**Files:**
- Create: `src/kg/coverage.py`
- Modify: `src/kg/registry.py`, `src/kg/gate.py`, `src/kg/paths.py`
- Test: `tests/test_coverage.py`, `tests/test_registry.py`

**Interfaces:**
- Produces `CoverageReport`, `CoverageEntry`.
- `CoverageReport.record(rel_path, status, reason=None, outputs=None) -> None`
- `CoverageReport.write_atomic(path) -> None`
- `Registry.mark_chunk(source_sha, chunk, status, reason=None) -> None`

- [ ] **Step 1: Write failing checkpoint test**

```python
def test_first_completed_chunk_keeps_source_partial(tmp_path):
    registry = Registry(tmp_path / "registry.jsonl")
    source = registry.add_raw("body", title="x", source_type="text", chunk_count=3)
    registry.mark_chunk(source.sha256, 0, "done")
    record = registry.get(source.sha256)
    assert record.status == "partial"
    assert record.chunks_done == [0]
```

- [ ] **Step 2: Write failing coverage test**

```python
def test_coverage_writes_machine_readable_counts(tmp_path):
    report = CoverageReport(generation=7, mode="fast")
    report.record("a.py", "indexed", outputs=["code:p:a.py"])
    report.record("broken.ts", "failed", reason="parse_error")
    report.write_atomic(tmp_path / "coverage.json")
    body = json.loads((tmp_path / "coverage.json").read_text())
    assert body["sources"] == {"seen": 2, "indexed": 1, "skipped": 0, "failed": 1, "stale": 0}
    assert body["by_reason"]["parse_error"] == 1
```

- [ ] **Step 3: Run failure checks**

Run: `uv run pytest tests/test_coverage.py tests/test_registry.py -v`
Expected: FAIL on missing types/methods.

- [ ] **Step 4: Implement checkpoint state machine**

States: `pending` when no chunks done; `partial` when `0 < done+failed < chunk_count`; `extracted` only when `done+failed == chunk_count`; `failed` only when every chunk failed. Replace any whole-source extracted mutation in gate/save flow with `mark_chunk()`.

- [ ] **Step 5: Implement coverage**

Serialize:

```json
{"generation":7,"mode":"fast","sources":{"seen":2,"indexed":1,"skipped":0,"failed":1,"stale":0},"by_reason":{"parse_error":1},"outputs":{"nodes":1,"edges":0}}
```

Use same temp/fsync/replace helper pattern as Task 1.

- [ ] **Step 6: Run tests**

Run: `uv run pytest tests/test_coverage.py tests/test_registry.py -v`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add src/kg/coverage.py src/kg/registry.py src/kg/gate.py src/kg/paths.py tests/test_coverage.py tests/test_registry.py
git commit -m "fix: track extraction checkpoints per chunk"
```

## Task 3: Staged Rebuild + Atomic Publish

**Files:**
- Create: `src/kg/rebuild.py`
- Modify: `src/kg/paths.py`, `src/kg/snapshot.py`
- Test: `tests/test_rebuild.py`

**Interfaces:**
- Produces `WriterLock`, `RebuildCandidate`, `rebuild()`.
- `WriterLock.acquire(root) -> WriterLock`, `release() -> None`
- `RebuildCandidate.create(paths) -> RebuildCandidate`
- `RebuildCandidate.validate() -> None`
- `RebuildCandidate.publish() -> None`

- [ ] **Step 1: Write failing atomic publish test**

```python
def test_publish_replaces_live_db_only_after_integrity_validation(tmp_path):
    paths = init_project(tmp_path, user_id="u", scope="p")
    SQLiteAdapter(paths.kg_db).upsert_nodes([Node(id="u:object:old", type="object", name="old")])
    candidate = RebuildCandidate.create(paths)
    SQLiteAdapter(candidate.db_path).upsert_nodes([Node(id="u:object:new", type="object", name="new")])
    candidate.publish()
    live = SQLiteAdapter(paths.kg_db)
    assert live.get("u:object:old") is None
    assert live.get("u:object:new") is not None
```

- [ ] **Step 2: Write failing recovery/lock tests**

```python
def test_failed_candidate_never_replaces_live_db(tmp_path): ...
def test_second_writer_lock_fails_without_stealing(tmp_path): ...
```

- [ ] **Step 3: Run failure check**

Run: `uv run pytest tests/test_rebuild.py -v`
Expected: FAIL, missing module.

- [ ] **Step 4: Implement staging lifecycle**

Use `.kg/.rebuild-<uuid>/kg.db`; `WriterLock` atomically calls `Path.mkdir()` for `.kg/.writer.lock`, writes `{pid, hostname, created_at}`. Existing lock raises `RuntimeError` with metadata. Copy current live DB into candidate for incremental operation; fresh candidate for full rebuild.

`validate()` opens candidate adapter, runs `PRAGMA integrity_check`, asserts result `ok`. `publish()` closes candidate connection, fsyncs file, replaces live DB with `os.replace`, fsyncs `.kg` parent on POSIX. On error, leave candidate for `recover`; never remove live DB.

- [ ] **Step 5: Run tests**

Run: `uv run pytest tests/test_rebuild.py -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/kg/rebuild.py src/kg/paths.py src/kg/snapshot.py tests/test_rebuild.py
git commit -m "feat: add staged atomic graph rebuild"
```

## Task 4: Index Config + Orchestration Skeleton

**Files:**
- Create: `src/kg/index.py`
- Modify: `src/kg/config.py`
- Test: `tests/test_index.py`

**Interfaces:**
- Produces `IndexMode`, `IndexPlan`, `IndexResult`, `plan_index()`.
- `plan_index(paths, root, mode, extractor_version) -> IndexPlan`
- Modes: `fast`, `moderate`, `full`.

- [ ] **Step 1: Write failing mode policy tests**

```python
def test_fast_mode_disables_embeddings_and_semantic_extraction(tmp_path):
    plan = plan_index(paths, root, mode="fast", extractor_version="v1")
    assert plan.run_structural is True
    assert plan.run_embeddings is False
    assert plan.run_document_extraction is False

def test_moderate_mode_keeps_existing_document_behavior(tmp_path):
    plan = plan_index(paths, root, mode="moderate", extractor_version="v1")
    assert plan.run_embeddings is True
    assert plan.run_document_extraction is True
```

- [ ] **Step 2: Run failure check**

Run: `uv run pytest tests/test_index.py -v`
Expected: FAIL, missing module.

- [ ] **Step 3: Add config**

```python
class IndexConfig(BaseModel):
    mode: str = "moderate"
    incremental_threshold: float = 0.10
    batch_size: int = 1000
```

Render/parse `[index]` in `Config.render_toml()`.

- [ ] **Step 4: Implement `IndexPlan`**

`fast`: structural only. `moderate`: structural + embeddings + document extraction. `full`: moderate + similarity/community jobs. Validate mode input; compare file-hash changes against `incremental_threshold`; choose `incremental` or `full_rebuild` action. No parser implementation in this task.

- [ ] **Step 5: Run tests**

Run: `uv run pytest tests/test_index.py -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/kg/index.py src/kg/config.py tests/test_index.py
git commit -m "feat: add index mode planning"
```

## Task 5: Tree-Sitter Code Projection

**Files:**
- Create: `src/kg/code_index.py`
- Create: `tests/fixtures/code_index/sample.py`
- Create: `tests/fixtures/code_index/sample.ts`
- Test: `tests/test_code_index.py`
- Modify: `pyproject.toml`

**Interfaces:**
- Produces `CodeProjection`, `CodeNode`, `CodeEdge`, `project_code_file()`.
- `project_code_file(path, project, source_sha256, parser_version) -> CodeProjection`

- [ ] **Step 1: Write failing Python projection test**

```python
def test_python_projection_emits_file_class_function_and_containment():
    projection = project_code_file(FIXTURES / "sample.py", "demo", "sha", "v1")
    assert {(n.subtype, n.qualified_name) for n in projection.nodes} >= {
        ("code_file", "sample.py"), ("class", "sample.py:Greeter"),
        ("function", "sample.py:Greeter.greet"), ("function", "sample.py:main"),
    }
    assert any(e.semantic_type == "part_of" for e in projection.edges)
```

- [ ] **Step 2: Write failing TypeScript projection test**

```python
def test_typescript_projection_emits_exported_function_and_class():
    projection = project_code_file(FIXTURES / "sample.ts", "demo", "sha", "v1")
    assert {n.qualified_name for n in projection.nodes} >= {"sample.ts:Api", "sample.ts:fetchUser"}
```

- [ ] **Step 3: Run failure check**

Run: `uv run pytest tests/test_code_index.py -v`
Expected: FAIL, missing module.

- [ ] **Step 4: Add minimal parser dependencies**

Add pinned Tree-sitter packages for Python + JavaScript/TypeScript only. Do not add LSP or multi-language frameworks.

- [ ] **Step 5: Implement deterministic projection**

Use parser AST nodes for module/file, class declaration, function/method declaration. Emit `object`-compatible projected records:

```python
CodeNode(
  id="code:demo:sample.py#Greeter.greet",
  type="object", subtype="function", name="greet",
  qualified_name="sample.py:Greeter.greet",
  attributes={"path":"sample.py","start_line":4,"end_line":5,"sha256":"sha","parser_version":"v1"}
)
```

Emit `part_of` for class/function → file or method → class. Emit imports only when grammar node yields a literal module specifier; place relation exactness in attributes. Never infer calls.

- [ ] **Step 6: Run tests**

Run: `uv run pytest tests/test_code_index.py -v`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add src/kg/code_index.py tests/fixtures/code_index tests/test_code_index.py pyproject.toml uv.lock
git commit -m "feat: add Python and TypeScript structural projection"
```

## Task 6: Index CLI Integration + End-to-End Coverage

**Files:**
- Create: `src/kg/cli/index_cli.py`
- Modify: `src/kg/cli/main.py`
- Modify: `src/kg/index.py`
- Test: `tests/test_index_cli.py`

**Interfaces:**
- Produces `kg index scan`, `kg index code`, `kg index rebuild`, `kg index status`, `kg index recover`.

- [ ] **Step 1: Write failing CLI E2E test**

```python
def test_index_scan_skips_unchanged_source_and_writes_coverage(tmp_path):
    runner = CliRunner()
    init_project(tmp_path, user_id="u", scope="p")
    (tmp_path / "a.py").write_text("def f(): pass\n")
    first = runner.invoke(app, ["index", "scan", str(tmp_path), "--mode", "fast"])
    assert first.exit_code == 0
    second = runner.invoke(app, ["index", "scan", str(tmp_path), "--mode", "fast"])
    assert "skipped: 1" in second.stdout
    coverage = json.loads((tmp_path / ".kg" / "coverage.json").read_text())
    assert coverage["sources"]["skipped"] == 1
```

- [ ] **Step 2: Run failure check**

Run: `uv run pytest tests/test_index_cli.py -v`
Expected: FAIL, command absent.

- [ ] **Step 3: Implement commands**

- `scan <path> --mode`: call `plan_index`, apply `FileHashRegistry`, project code when mode enables structural work, record coverage, atomically save cache/report.
- `code <path> --mode`: code-only alias to scan with document extraction disabled.
- `rebuild [--full]`: create candidate, execute scan/projection into staging, validate, publish.
- `status`: print compact source counts/reasons and rebuild generation.
- `recover`: list valid candidates; publish only candidate explicitly named by user.

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/test_index_cli.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/kg/cli/index_cli.py src/kg/cli/main.py src/kg/index.py tests/test_index_cli.py
git commit -m "feat: add incremental code index CLI"
```

## Task 7: Final Evaluation + Regression Rollback Gate

**Files:**
- Modify: `Makefile`
- Create: `bench/evaluate_cmb.py`
- Test: `tests/test_evaluate_cbm.py`

**Interfaces:**
- Produces `make evaluate-cbm` returning nonzero if any baseline floor fails.

- [ ] **Step 1: Write failing floor parser test**

```python
def test_evaluation_fails_when_graph_query_exceeds_floor(tmp_path):
    result = evaluate({"query_ms": 201, "recall_at_10": 1.0})
    assert result.ok is False
    assert "query_ms" in result.failures
```

- [ ] **Step 2: Run failure check**

Run: `uv run pytest tests/test_evaluate_cbm.py -v`
Expected: FAIL, missing module.

- [ ] **Step 3: Implement evaluator**

Run/test:

```bash
uv run pytest tests/ -x
PYTHONPATH=. uv run kg bench --scale 10 --dimension all
PYTHONPATH=. uv run pytest tests/test_scale_10k.py -v -s
PYTHONPATH=. uv run python bench/scale_100k.py
```

Parse output and require: zero failures, Recall@5 ≥0.90, Recall@10 ≥0.95, MRR ≥0.85, 100k build ≤10s, search ≤50ms, query ≤200ms, graph hot ≤500ms, 10k suite passes 5/5. Save raw outputs + `evaluation.json` under `bench/results/<UTC timestamp>/`.

- [ ] **Step 4: Add Make target**

```make
.PHONY: evaluate-cbm
evaluate-cbm:
	PYTHONPATH=$(CURDIR) uv run python bench/evaluate_cbm.py
```

- [ ] **Step 5: Run evaluation**

Run: `make evaluate-cbm`
Expected: PASS all floors or report exact failing metric.

- [ ] **Step 6: Commit**

```bash
git add Makefile bench/evaluate_cbm.py tests/test_evaluate_cbm.py
git commit -m "test: add CBM regression evaluation gate"
```

## Execution Order

1. Tasks 1 and 2 can execute in parallel.
2. Task 3 depends on Task 1 only for registry reconciliation rules.
3. Task 4 depends on Task 1 + Task 2.
4. Task 5 can execute in parallel with Tasks 1-4.
5. Task 6 depends on Tasks 1-5.
6. Task 7 runs after Task 6, then gates acceptance/revert.

## Self-Review

- P0 content SHA and filesystem hash are separate: covered in Task 1.
- First-chunk extraction bug is fixed: Task 2.
- Atomic publish and lock/recovery are independent from live writes: Task 3.
- Structural parser has bounded language/symbol scope: Task 5.
- Modes and coverage do not overload document extraction internals: Task 4 + Task 6.
- Final floors and revert decision are enforceable: Task 7.
