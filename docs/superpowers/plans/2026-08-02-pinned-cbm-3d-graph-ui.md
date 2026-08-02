# Pinned CBM 3D Graph UI Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace both kg graph frontends with the exact Codebase Memory 3D shell pinned at `d6be58ef9d43c574a2d1b0827ecc1e3c4846f0fe`, backed by one deterministic Python layout pipeline for live `kg viz` and static GitHub Pages.

**Architecture:** Vendor upstream `graph-ui/` verbatim, then confine kg-specific behavior to a small capability/transport adapter. The existing loopback Python server serves packaged Vite assets and CBM-shaped API responses; the same serializer writes the Pages snapshot. A Python port of CBM `layout3d.c` supplies deterministic coordinates without importing CBM's C HTTP server.

**Tech Stack:** Python 3.11+, stdlib `http.server`, existing kg SQLite storage and Louvain clustering, React 19, TypeScript, Vite 6, Three.js 0.183, React Three Fiber 9, Vitest 4, Hatch/uv, GitHub Actions.

## Global Constraints

- Pin upstream to full commit `d6be58ef9d43c574a2d1b0827ecc1e3c4846f0fe`; never track upstream `main` implicitly.
- Preserve upstream MIT license, source headers, tests, dependency versions, and provenance.
- Use one frontend source under `ui/graph-ui/`; remove the duplicate `docs/demo/src/` application.
- Use one Python normalization/layout/serialization pipeline for live and static graph payloads.
- Preserve current limits: 2,000 nodes, 4,000 edges, 4 MiB response body.
- Preserve loopback-only binding, CSP, path-traversal protection, and runtime network independence.
- Hide controls without real kg semantics; never fabricate ADR, dead-code, missed-call, code-snippet, or call-depth data.
- Since kg has no call graph, all primary layout anchors use `z = 0.0`; 3D depth may emerge only from force optimization.
- Unknown capabilities default to hidden. Static mode exposes no mutation controls.
- Node/npm is build-time only. Installed wheels must run `kg viz` without Node, npm, CDN access, or external assets.
- Use TDD for Python and adapter changes. Keep upstream frontend tests passing.
- Do not modify unrelated existing changes in `.planning/HANDOFF.json`, `.planning/.pending-auth-captures.jsonl`, or `.pi/`.

## Locked File Structure

| Path | Responsibility |
|---|---|
| `ui/graph-ui/` | Vendored pinned CBM frontend and tests |
| `ui/graph-ui/PROVENANCE.md` | Exact upstream pin, license, copied paths, local patch inventory |
| `ui/graph-ui/src/lib/kgAdapter.ts` | Runtime mode, capability loading, endpoint selection; sole kg-specific frontend boundary |
| `ui/graph-ui/src/lib/kgAdapter.test.ts` | Live/static fallback and fail-closed capability tests |
| `src/kg/viz/layout3d.py` | Pure deterministic 3D layout port |
| `src/kg/viz/api.py` | Graph normalization, CBM serializers, capabilities, repo/project metadata |
| `src/kg/viz/indexing.py` | Validated single-flight indexing job state |
| `src/kg/viz/server.py` | Loopback HTTP routing, packaged static assets, CSP, ETag, endpoint wiring |
| `src/kg/viz/assets/` | Generated Vite output included in the wheel; never hand-edited |
| `scripts/build_graph_ui.py` | Build snapshot, run npm build, copy dist into package assets |
| `tests/viz/` | Focused layout, API, indexing, server, snapshot, package tests |
| `docs/demo/graph.json` | Retained static source dataset until moved by Task 7 |

---

### Task 1: Vendor the pinned upstream frontend

**Files:**
- Create: `ui/graph-ui/**`
- Create: `ui/graph-ui/PROVENANCE.md`
- Create: `ui/graph-ui/LICENSE.upstream`
- Test: upstream tests under `ui/graph-ui/src/**/*.test.ts*`

**Interfaces:**
- Consumes: upstream Git repository at full commit `d6be58ef9d43c574a2d1b0827ecc1e3c4846f0fe`.
- Produces: an npm project at `ui/graph-ui/` with unchanged upstream source, lockfile, tests, and `npm run build` output in `ui/graph-ui/dist/`.

- [ ] **Step 1: Copy the exact upstream tree**

```bash
tmp="$(mktemp -d)"
git clone --filter=blob:none https://github.com/DeusData/codebase-memory-mcp.git "$tmp/cbm"
git -C "$tmp/cbm" checkout d6be58ef9d43c574a2d1b0827ecc1e3c4846f0fe
mkdir -p ui
cp -R "$tmp/cbm/graph-ui" ui/graph-ui
cp "$tmp/cbm/LICENSE" ui/graph-ui/LICENSE.upstream
rm -rf ui/graph-ui/node_modules ui/graph-ui/dist
rm -f ui/graph-ui/tsconfig.tsbuildinfo
```

Expected: `git -C "$tmp/cbm" rev-parse HEAD` prints `d6be58ef9d43c574a2d1b0827ecc1e3c4846f0fe`; no generated dependency/build directories remain.

- [ ] **Step 2: Record provenance and the initial empty patch set**

Create `ui/graph-ui/PROVENANCE.md`:

```markdown
# Codebase Memory Graph UI Provenance

- Upstream: https://github.com/DeusData/codebase-memory-mcp
- Commit: `d6be58ef9d43c574a2d1b0827ecc1e3c4846f0fe`
- Copied path: `graph-ui/`
- License: MIT; see `LICENSE.upstream`

## Local patches

Each local frontend change must be listed here with its file paths and reason.
The initial vendored tree contains no source patches.

## Refresh procedure

1. Copy `graph-ui/` from a reviewed upstream commit.
2. Update the full commit above.
3. Reapply every patch listed in this file.
4. Run `npm test` and `npm run build` in `ui/graph-ui/`.
5. Run `make test` and inspect the vendored source diff.
```

- [ ] **Step 3: Verify the pinned upstream tests and build before patching**

Run:

```bash
cd ui/graph-ui && npm ci && npm test -- --run && npm run build
```

Expected: all upstream Vitest files pass; TypeScript and Vite build finish successfully.

- [ ] **Step 4: Commit the vendored snapshot**

```bash
git add ui/graph-ui
git commit -m "chore(ui): vendor pinned CBM graph frontend" \
  -m "Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

### Task 2: Port deterministic CBM 3D layout

**Files:**
- Create: `src/kg/viz/layout3d.py`
- Create: `tests/viz/test_layout3d.py`

**Interfaces:**
- Consumes: `LayoutNodeInput(id: str, cluster_key: str, name: str, label: str, degree: int)` and `LayoutEdgeInput(source: str, target: str, type: str)`.
- Produces: `layout_graph(nodes, edges) -> LayoutResult`; `LayoutResult.nodes` contains `PositionedNode(render_id: int, kg_id: str, x: float, y: float, z: float, size: float)`; `LayoutResult.edges` contains `PositionedEdge(source: int, target: int, type: str)`.
- Guarantees: stable render IDs, finite values, deterministic output, absent dangling edges, flat semantic anchor `z=0.0`.

- [ ] **Step 1: Write failing primitive and determinism tests**

Create `tests/viz/test_layout3d.py`:

```python
from math import isfinite

from kg.viz.layout3d import (
    LayoutEdgeInput,
    LayoutNodeInput,
    fnv1a_32,
    layout_graph,
)


def _nodes() -> list[LayoutNodeInput]:
    return [
        LayoutNodeInput("b", "fact", "Beta", "fact", 1),
        LayoutNodeInput("a", "document/docs", "Alpha", "document", 2),
        LayoutNodeInput("c", "fact", "Gamma", "fact", 1),
    ]


def _edges() -> list[LayoutEdgeInput]:
    return [
        LayoutEdgeInput("a", "b", "mentions"),
        LayoutEdgeInput("missing", "a", "mentions"),
        LayoutEdgeInput("b", "c", "related_to"),
    ]


def test_fnv1a_matches_upstream_unsigned_32_bit_hash() -> None:
    assert fnv1a_32(b"") == 0x811C9DC5
    assert fnv1a_32(b"fact") == 0xA30C2E93


def test_layout_is_deterministic_finite_and_stably_numbered() -> None:
    first = layout_graph(_nodes(), _edges())
    second = layout_graph(list(reversed(_nodes())), list(reversed(_edges())))

    assert first == second
    assert [(n.kg_id, n.render_id) for n in first.nodes] == [
        ("a", 0), ("b", 1), ("c", 2)
    ]
    assert [(e.source, e.target, e.type) for e in first.edges] == [
        (0, 1, "mentions"), (1, 2, "related_to")
    ]
    assert all(isfinite(v) for n in first.nodes for v in (n.x, n.y, n.z, n.size))


def test_layout_has_no_fabricated_call_depth_layer() -> None:
    result = layout_graph(_nodes(), _edges())
    assert all(node.z == 0.0 for node in result.nodes)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/viz/test_layout3d.py -q`

Expected: collection fails with `ModuleNotFoundError: No module named 'kg.viz.layout3d'`.

- [ ] **Step 3: Implement immutable inputs, outputs, hash, seed ring, and edge normalization**

Create `src/kg/viz/layout3d.py` with these public definitions:

```python
from __future__ import annotations

from dataclasses import dataclass
from math import cos, isfinite, pi, sin, sqrt

BH_THETA = 1.2
OCTREE_MAX_DEPTH = 26
OCTREE_MIN_HALF = 1e-4
LOCAL_REPULSION = 8.0
LOCAL_ATTRACTION = 1.0
LOCAL_ANCHOR_K = 0.25
LOCAL_ITERATIONS = 40
MAX_DISPLACEMENT = 8.0


@dataclass(frozen=True, slots=True)
class LayoutNodeInput:
    id: str
    cluster_key: str
    name: str
    label: str
    degree: int


@dataclass(frozen=True, slots=True)
class LayoutEdgeInput:
    source: str
    target: str
    type: str


@dataclass(frozen=True, slots=True)
class PositionedNode:
    render_id: int
    kg_id: str
    x: float
    y: float
    z: float
    size: float


@dataclass(frozen=True, slots=True)
class PositionedEdge:
    source: int
    target: int
    type: str


@dataclass(frozen=True, slots=True)
class LayoutResult:
    nodes: tuple[PositionedNode, ...]
    edges: tuple[PositionedEdge, ...]


def fnv1a_32(value: bytes) -> int:
    result = 0x811C9DC5
    for byte in value:
        result ^= byte
        result = (result * 0x01000193) & 0xFFFFFFFF
    return result
```

Implement upstream's 32-bit LCG exactly: update with `(seed * 1103515245 + 12345) & 0xFFFFFFFF`; derive jitter from `((seed >> 16) & 0x7FFF) / 32768.0 - 0.5`. Sort nodes by `id`; assign render IDs by sorted order; sort retained edges by `(source render ID, target render ID, type)`; discard edges whose endpoint is absent. Seed each node at radius `500 + ((cluster_hash >> 16) & 0xFF) / 255 * 250`, cluster angle `(cluster_hash & 0xFFFF) / 65535 * 2π`, deterministic ±20 jitter, and `z=0.0`. Set `size=max(1.0, sqrt(degree + 1.0))`.

- [ ] **Step 4: Implement Barnes-Hut optimization using the pinned constants**

Inside `layout3d.py`, add private `_Octree`/`_Octant` helpers with center, half-width, aggregate mass, center of mass, optional point index, and eight children. Implement:

1. insertion until one point per leaf, capped by `OCTREE_MAX_DEPTH` and `OCTREE_MIN_HALF`;
2. repulsion approximation when `cell_width / distance < BH_THETA`;
3. exact edge attraction using `LOCAL_ATTRACTION`;
4. anchor spring `(anchor - position) * LOCAL_ANCHOR_K * (degree + 1)`;
5. displacement clamp to `MAX_DISPLACEMENT` per iteration;
6. 40 iterations, reduced to 20 above 100,000 nodes and 10 above 500,000 nodes;
7. optimization only in `x/y`; retain every `z=0.0` because kg lacks call-depth semantics;
8. non-finite output normalization to `0.0`, and non-finite size normalization to `1.0`.

Keep the optimizer private. `layout_graph()` remains the sole orchestration entry point.

- [ ] **Step 5: Run layout tests**

Run: `uv run pytest tests/viz/test_layout3d.py -q`

Expected: all tests pass.

- [ ] **Step 6: Add a pinned fixture assertion for byte stability**

Append:

```python
import json
from dataclasses import asdict


def test_layout_serialization_is_byte_stable() -> None:
    result = layout_graph(_nodes(), _edges())
    encoded = json.dumps(
        {
            "nodes": [asdict(node) for node in result.nodes],
            "edges": [asdict(edge) for edge in result.edges],
        },
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    assert encoded == json.dumps(json.loads(encoded), sort_keys=True, separators=(",", ":"))
```

Run: `uv run pytest tests/viz/test_layout3d.py -q`

Expected: all tests pass.

- [ ] **Step 7: Commit**

```bash
git add src/kg/viz/layout3d.py tests/viz/test_layout3d.py
git commit -m "feat(viz): add deterministic CBM 3D layout" \
  -m "Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

### Task 3: Normalize kg data and serialize the CBM API contract

**Files:**
- Create: `src/kg/viz/api.py`
- Create: `tests/viz/test_api.py`
- Modify: `tests/viz/test_server.py`
- Modify: `tests/test_viz_cache.py`

**Interfaces:**
- Consumes: `StorageAdapter`, `louvain_cached(adapter)`, and `layout_graph()` from Task 2.
- Produces: `build_layout_payload(adapter, *, max_nodes=2000, max_edges=4000) -> dict[str, object]`, `build_capabilities(*, static: bool) -> dict[str, bool]`, `build_project_payload(adapter, project_name)`, `build_schema_payload(adapter)`, `json_bytes(payload) -> bytes`.
- Payload node fields: `id`, `kg_id`, `x`, `y`, `z`, `label`, `name`, optional `file_path`, `size`, `color`, `in_calls`; no fabricated `status`, `qualified_name`, or source lines.

- [ ] **Step 1: Write failing contract, truncation, and capability tests**

Create `tests/viz/test_api.py` using the repository's existing SQLite adapter fixture pattern:

```python
import json

from kg.ontology import Edge, Node
from kg.storage.sqlite import SQLiteAdapter
from kg.viz.api import build_capabilities, build_layout_payload, json_bytes


def _seed(adapter: SQLiteAdapter) -> None:
    adapter.upsert_nodes([
        Node(id="doc:b", type="document", name="B", attributes={"path": "docs/b.md"}),
        Node(id="fact:a", type="fact", name="A"),
        Node(id="fact:c", type="fact", name="C"),
    ])
    adapter.upsert_edges([
        Edge(id="e2", source="doc:b", target="fact:c", semantic_type="mentions"),
        Edge(id="e1", source="fact:a", target="doc:b", semantic_type="related_to"),
    ])


def test_layout_payload_matches_pinned_cbm_shape(tmp_path) -> None:
    adapter = SQLiteAdapter(tmp_path / "graph.db")
    _seed(adapter)
    payload = build_layout_payload(adapter)

    assert set(payload) == {
        "nodes", "edges", "total_nodes", "linked_projects", "truncated_nodes", "truncated_edges"
    }
    assert payload["total_nodes"] == 3
    assert [n["kg_id"] for n in payload["nodes"]] == ["doc:b", "fact:a", "fact:c"]
    assert all(set(("id", "kg_id", "x", "y", "z", "label", "name", "size", "color", "in_calls")) <= set(n) for n in payload["nodes"])
    assert all("status" not in n and "qualified_name" not in n for n in payload["nodes"])
    assert payload["nodes"][0]["file_path"] == "docs/b.md"
    json.loads(json_bytes(payload))


def test_layout_payload_reports_limits(tmp_path) -> None:
    adapter = SQLiteAdapter(tmp_path / "graph.db")
    _seed(adapter)
    payload = build_layout_payload(adapter, max_nodes=2, max_edges=1)
    assert len(payload["nodes"]) == 2
    assert len(payload["edges"]) <= 1
    assert payload["truncated_nodes"] is True
    assert payload["truncated_edges"] is True


def test_capabilities_fail_closed_and_static_has_no_mutations() -> None:
    assert build_capabilities(static=True) == {
        "graph": True,
        "projects": False,
        "control": False,
        "index": False,
        "code_view": False,
        "adr": False,
        "dead_code": False,
        "missed_graph": False,
    }
    assert build_capabilities(static=False)["index"] is True
    assert build_capabilities(static=False)["adr"] is False
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/viz/test_api.py -q`

Expected: collection fails because `kg.viz.api` does not exist.

- [ ] **Step 3: Implement normalization and serialization**

Create `src/kg/viz/api.py`. Define constants `_MAX_NODES=2_000`, `_MAX_EDGES=4_000`, `_MAX_BYTES=4 * 1024 * 1024`, and a fixed accessible color map keyed by existing kg node type. Implement these signatures:

```python
def json_bytes(payload: object) -> bytes:
    return json.dumps(
        payload, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")


def build_capabilities(*, static: bool) -> dict[str, bool]: ...

def build_layout_payload(
    adapter: StorageAdapter,
    *,
    max_nodes: int = _MAX_NODES,
    max_edges: int = _MAX_EDGES,
) -> dict[str, object]: ...

def build_project_payload(adapter: StorageAdapter, project_name: str) -> dict[str, object]: ...

def build_schema_payload(adapter: StorageAdapter) -> dict[str, object]: ...
```

Normalization rules:

- order selected nodes by `id` before truncation;
- derive `cluster_key` from the first three components of `attributes["path"]` when it is a non-empty string; otherwise use `type`;
- derive `label` from kg `type`;
- use `summary` only in kg detail metadata, not `qualified_name`;
- include `file_path` only when `attributes["path"]` is a string;
- calculate degree from retained active edges;
- set `in_calls=0`; this is a numeric renderer hint, not a claim about calls;
- emit no `status`, `start_line`, `end_line`, or `qualified_name`;
- map edges through render IDs and omit dangling edges;
- use `linked_projects=[]`; omit `missed_graph` entirely;
- report full `total_nodes` plus booleans `truncated_nodes` and `truncated_edges`;
- reject `max_nodes < 1` or `max_edges < 0` with `ValueError`;
- reject serialized payloads larger than `_MAX_BYTES` with `PayloadTooLarge`.

`build_project_payload()` returns one local project with `name`, resolved `root_path`, and adapter generation as `indexed_at`. `build_schema_payload()` returns `{node_labels, edge_types, total_nodes, total_edges}` using active row counts and stable sorting.

- [ ] **Step 4: Run focused API tests**

Run: `uv run pytest tests/viz/test_api.py -q`

Expected: all tests pass.

- [ ] **Step 5: Adapt old 2D payload/cache tests to the new API boundary**

In `tests/viz/test_server.py` and `tests/test_viz_cache.py`, replace imports and assertions against `_graph_payload` with `build_layout_payload`. Preserve tests for active-row filtering, HTML escaping where still applicable, wiki traversal, loopback binding, generation invalidation, and ETag 304. Remove assertions specific to inline SVG or `/app.js`; Task 5 replaces them with packaged-asset assertions.

Run:

```bash
uv run pytest tests/viz/test_api.py tests/viz/test_server.py tests/test_viz_cache.py -q
```

Expected: all tests pass or only packaged-asset/server-route assertions remain pending Task 5; do not commit skipped tests.

- [ ] **Step 6: Commit**

```bash
git add src/kg/viz/api.py tests/viz/test_api.py tests/viz/test_server.py tests/test_viz_cache.py
git commit -m "feat(viz): expose CBM graph data contract" \
  -m "Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

### Task 4: Add validated single-flight indexing state

**Files:**
- Create: `src/kg/viz/indexing.py`
- Create: `tests/viz/test_indexing.py`

**Interfaces:**
- Consumes: an injected `runner(root: Path, project_name: str) -> None`; production wiring calls the existing kg index command boundary, not shell text.
- Produces: `IndexManager.start(root_path, project_name) -> IndexJob`, `IndexManager.status() -> list[dict[str, object]]`, `IndexBusy`, and `InvalidProjectPath`.
- Guarantees: one active job, resolved existing directory, explicit terminal state, no command injection.

- [ ] **Step 1: Write failing validation and lock tests**

Create `tests/viz/test_indexing.py`:

```python
from threading import Event

import pytest

from kg.viz.indexing import IndexBusy, IndexManager, InvalidProjectPath


def test_rejects_missing_or_relative_project_path(tmp_path) -> None:
    manager = IndexManager(lambda root, name: None)
    with pytest.raises(InvalidProjectPath):
        manager.start("relative/path", "demo")
    with pytest.raises(InvalidProjectPath):
        manager.start(str(tmp_path / "missing"), "demo")


def test_allows_only_one_index_job(tmp_path) -> None:
    entered, release = Event(), Event()

    def runner(root, name):
        entered.set()
        release.wait(timeout=2)

    manager = IndexManager(runner)
    first = manager.start(str(tmp_path), "demo")
    assert entered.wait(timeout=1)
    with pytest.raises(IndexBusy):
        manager.start(str(tmp_path), "second")
    release.set()
    first.thread.join(timeout=2)
    assert manager.status()[0]["status"] == "done"


def test_records_runner_error(tmp_path) -> None:
    def runner(root, name):
        raise RuntimeError("index failed")

    manager = IndexManager(runner)
    job = manager.start(str(tmp_path), "demo")
    job.thread.join(timeout=2)
    assert manager.status()[0]["status"] == "error"
    assert manager.status()[0]["error"] == "index failed"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/viz/test_indexing.py -q`

Expected: collection fails because `kg.viz.indexing` does not exist.

- [ ] **Step 3: Implement the minimum thread-safe manager**

Create `src/kg/viz/indexing.py` with:

```python
@dataclass(slots=True)
class IndexJob:
    slot: int
    path: str
    project_name: str
    status: Literal["indexing", "done", "error"]
    error: str | None
    thread: Thread


class IndexBusy(RuntimeError):
    pass


class InvalidProjectPath(ValueError):
    pass


class IndexManager:
    def __init__(self, runner: Callable[[Path, str], None]) -> None: ...
    def start(self, root_path: str, project_name: str) -> IndexJob: ...
    def status(self) -> list[dict[str, object]]: ...
```

Resolve with `Path(root_path).expanduser().resolve(strict=True)`. Require an absolute input, a directory result, and project names matching `[A-Za-z0-9._-]{1,128}`. Protect job state with `threading.Lock`. Start one daemon `Thread`; set `done` or `error` in `finally`-safe worker code. Never invoke a shell. Return copies from `status()` with only `slot`, `status`, `path`, and `error`.

- [ ] **Step 4: Run indexing tests**

Run: `uv run pytest tests/viz/test_indexing.py -q`

Expected: all tests pass.

- [ ] **Step 5: Commit**

```bash
git add src/kg/viz/indexing.py tests/viz/test_indexing.py
git commit -m "feat(viz): add single-flight index jobs" \
  -m "Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

### Task 5: Serve packaged UI assets and CBM-compatible endpoints

**Files:**
- Modify: `src/kg/viz/server.py`
- Modify: `src/kg/cli/viz.py`
- Create: `src/kg/viz/assets/.gitkeep` initially; generated files replace it in Task 7
- Modify: `tests/viz/test_server.py`
- Create: `tests/viz/test_server_api.py`

**Interfaces:**
- Consumes: Task 3 API builders, Task 4 `IndexManager`, package resource directory `kg.viz/assets`.
- Produces: `make_handler(adapter_factory, wiki_dir, *, assets, index_manager, project_name, project_root)`, live routes `/api/layout`, `/api/capabilities`, `/api/repo-info`, `/api/index`, `/api/index-status`, `/api/ui-config`, `/rpc`, `/wiki/<slug>`, and SPA assets.
- Preserves: `serve(adapter, port=9749, *, wiki_dir=None, open_browser=False)` caller compatibility and `127.0.0.1` binding.

- [ ] **Step 1: Write failing route, CSP, path, and payload-limit tests**

In `tests/viz/test_server_api.py`, start `ThreadingHTTPServer` with a temporary asset directory containing `index.html`, `assets/app.js`, and `assets/app.css`. Assert:

```python
def test_live_routes_are_self_contained(http_server):
    assert get(http_server, "/").status == 200
    assert get(http_server, "/assets/app.js").headers["Content-Type"].startswith("text/javascript")
    assert get(http_server, "/api/capabilities").json()["graph"] is True
    assert get(http_server, "/api/capabilities").json()["adr"] is False
    layout = get(http_server, "/api/layout?max_nodes=2000").json()
    assert {"nodes", "edges", "total_nodes"} <= layout.keys()


def test_spa_asset_traversal_is_rejected(http_server):
    assert get(http_server, "/assets/../../pyproject.toml").status in (400, 404)


def test_index_validation_and_busy_status(http_server, blocking_index_runner, tmp_path):
    bad = post_json(http_server, "/api/index", {"root_path": "relative", "project_name": "x"})
    assert bad.status == 400
    accepted = post_json(http_server, "/api/index", {"root_path": str(tmp_path), "project_name": "x"})
    assert accepted.status == 202
    busy = post_json(http_server, "/api/index", {"root_path": str(tmp_path), "project_name": "y"})
    assert busy.status == 429


def test_csp_allows_required_local_threejs_features(http_server):
    csp = get(http_server, "/").headers["Content-Security-Policy"]
    assert "script-src 'self' 'wasm-unsafe-eval'" in csp
    assert "worker-src 'self' blob:" in csp
    assert "object-src 'none'" in csp
```

Use stdlib `http.client`; add no HTTP test dependency.

- [ ] **Step 2: Run server API tests to verify failure**

Run: `uv run pytest tests/viz/test_server_api.py -q`

Expected: new endpoint and asset tests fail against the old inline-SVG server.

- [ ] **Step 3: Replace inline `_HTML` and `_JS` with safe asset serving**

Delete the inline 2D constants. Resolve assets with `importlib.resources.files("kg.viz").joinpath("assets")` by default; accept an injected `assets` traversable in tests. For each request:

- map `/` to `index.html`;
- normalize URL paths with `PurePosixPath` and reject `..`;
- serve only files beneath the asset root;
- use `mimetypes.guess_type` and explicit UTF-8/content length;
- fall back to `index.html` only for extensionless SPA routes;
- return 404 for absent files with extensions;
- retain `/wiki/<slug>` through `_resolve_wiki_page`.

Use this CSP exactly:

```python
_CSP = (
    "default-src 'self'; connect-src 'self'; img-src 'self' data: blob:; "
    "script-src 'self' 'wasm-unsafe-eval'; style-src 'self' 'unsafe-inline'; "
    "font-src 'self' data:; worker-src 'self' blob:; object-src 'none'; "
    "base-uri 'none'; frame-ancestors 'none'"
)
```

- [ ] **Step 4: Wire read-only API endpoints and JSON-RPC equivalents**

Implement:

- `GET /api/layout?max_nodes=N`: parse decimal N, clamp to 2,000, call `build_layout_payload`;
- `GET /api/capabilities`: `build_capabilities(static=False)`;
- `GET /api/repo-info`: return resolved root, current branch/remote when obtainable through `git config`/`git branch --show-current` via argument-list subprocesses; strip URL credentials; empty strings when absent;
- `GET /api/ui-config`: `{"lang":"en","upstream_issues_url":"https://github.com/DeusData/codebase-memory-mcp/issues/new"}`;
- `POST /rpc`: accept only JSON-RPC `tools/call` for `list_projects` and `get_graph_schema`; return MCP-wrapped JSON text; reject every other method/tool with JSON-RPC `-32601`;
- no route for ADR, code snippets, missed calls, project deletion, process logs, or arbitrary filesystem browsing.

Return JSON errors as `{"error":{"code":"<stable_code>","message":"<safe message>"}}`. Convert `PayloadTooLarge` to 413 and invalid query/body to 400. Never include tracebacks.

- [ ] **Step 5: Wire index endpoints**

- `POST /api/index`: require JSON object body ≤64 KiB with string `root_path` and `project_name`; map `InvalidProjectPath` to 400, `IndexBusy` to 429, accepted jobs to 202 `{status,slot,path}`.
- `GET /api/index-status`: return `IndexManager.status()`.
- Construct one `IndexManager` in `serve()`, not per request.
- Wire its runner directly to the existing Python indexing entry boundary discovered in `src/kg/cli/index.py`; refactor that CLI file only enough to expose a callable `index_project(root: Path, project_name: str) -> None` if no callable exists. Preserve CLI behavior and add its existing CLI tests to the focused run.

- [ ] **Step 6: Preserve generation ETags and loopback-only startup**

Use adapter generation as the `/api/layout` ETag. Honor matching `If-None-Match` with 304. Keep `ThreadingHTTPServer(("127.0.0.1", port), handler)`. Open the browser only when `open_browser=True`.

Run:

```bash
uv run pytest tests/viz/test_server.py tests/viz/test_server_api.py tests/test_viz_cache.py -q
```

Expected: all tests pass.

- [ ] **Step 7: Commit**

```bash
git add src/kg/viz/server.py src/kg/cli/viz.py src/kg/viz/assets tests/viz/test_server.py tests/viz/test_server_api.py tests/test_viz_cache.py
git commit -m "feat(viz): serve CBM UI and live APIs" \
  -m "Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

### Task 6: Add the fail-closed kg frontend adapter and capability gates

**Files:**
- Create: `ui/graph-ui/src/lib/kgAdapter.ts`
- Create: `ui/graph-ui/src/lib/kgAdapter.test.ts`
- Modify: `ui/graph-ui/src/App.tsx`
- Modify: `ui/graph-ui/src/components/GraphTab.tsx`
- Modify: `ui/graph-ui/src/components/StatsTab.tsx`
- Modify: `ui/graph-ui/src/components/ControlTab.tsx`
- Modify: `ui/graph-ui/src/components/NodeDetailPanel.tsx`
- Modify: `ui/graph-ui/src/hooks/useGraphData.ts`
- Modify: `ui/graph-ui/PROVENANCE.md`

**Interfaces:**
- Consumes: live `/api/capabilities` and `/api/layout`; static `./graph-snapshot.json` and `./capabilities.json`.
- Produces: `loadRuntime(): Promise<RuntimeConfig>`, `graphUrl(runtime, project, maxNodes): string`, `CapabilitySet`, and fail-closed `DEFAULT_CAPABILITIES`.
- Guarantees: graph always available when valid; optional controls rendered only on explicit `true`; no fake disabled controls.

- [ ] **Step 1: Write failing adapter tests**

Create `ui/graph-ui/src/lib/kgAdapter.test.ts`:

```typescript
import { afterEach, describe, expect, it, vi } from "vitest";
import {
  DEFAULT_CAPABILITIES,
  graphUrl,
  loadRuntime,
} from "./kgAdapter";

afterEach(() => vi.unstubAllGlobals());

describe("kg adapter", () => {
  it("uses explicit live capabilities", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ graph: true, projects: true, control: true, index: true }),
    }));
    const runtime = await loadRuntime();
    expect(runtime.mode).toBe("live");
    expect(runtime.capabilities.adr).toBe(false);
    expect(graphUrl(runtime, "kg", 2000)).toBe("/api/layout?project=kg&max_nodes=2000");
  });

  it("falls back to static snapshot when the live endpoint is absent", async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce({ ok: false, status: 404 })
      .mockResolvedValueOnce({ ok: true, json: async () => ({ graph: true }) });
    vi.stubGlobal("fetch", fetchMock);
    const runtime = await loadRuntime();
    expect(runtime.mode).toBe("static");
    expect(runtime.capabilities).toEqual(DEFAULT_CAPABILITIES);
    expect(graphUrl(runtime, "ignored", 2000)).toBe("./graph-snapshot.json");
  });

  it("fails closed for unknown capability keys", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ graph: true, adr: "yes", code_view: 1 }),
    }));
    const runtime = await loadRuntime();
    expect(runtime.capabilities.adr).toBe(false);
    expect(runtime.capabilities.code_view).toBe(false);
  });
});
```

- [ ] **Step 2: Run test to verify failure**

Run: `cd ui/graph-ui && npm test -- --run src/lib/kgAdapter.test.ts`

Expected: fails because `kgAdapter.ts` does not exist.

- [ ] **Step 3: Implement runtime detection and capability parsing**

Create `kgAdapter.ts` with exact types:

```typescript
export type CapabilitySet = {
  graph: boolean;
  projects: boolean;
  control: boolean;
  index: boolean;
  code_view: boolean;
  adr: boolean;
  dead_code: boolean;
  missed_graph: boolean;
};

export type RuntimeConfig = {
  mode: "live" | "static";
  capabilities: CapabilitySet;
};

export const DEFAULT_CAPABILITIES: CapabilitySet = {
  graph: true,
  projects: false,
  control: false,
  index: false,
  code_view: false,
  adr: false,
  dead_code: false,
  missed_graph: false,
};

export async function loadRuntime(): Promise<RuntimeConfig> { ... }
export function graphUrl(runtime: RuntimeConfig, project: string, maxNodes: number): string { ... }
```

`loadRuntime()` first fetches `/api/capabilities`. On 404 or network failure, fetches `./capabilities.json`; static parsing may only turn `graph` on, never mutation capabilities. Live parsing accepts only literal booleans and defaults absent/invalid keys to `false`. URL-encode the project and clamp max nodes to `1..2000`.

- [ ] **Step 4: Gate the shell without rewriting upstream presentation components**

Load runtime once in `App.tsx` before rendering tabs. Pass `runtime` down through existing props; do not add a global state library.

- Always render Graph when `graph=true`.
- Render Projects selection only when `projects=true`.
- Render Control tab only when `control=true`.
- In `StatsTab`, hide ADR, browse, deletion, and indexing sections independently; render indexing only when `index=true`.
- In `NodeDetailPanel`, hide source-code lookup/link controls when `code_view=false`.
- In `GraphTab`, hide dead-code filters when `dead_code=false` and missed-graph UI when `missed_graph=false`.
- In `useGraphData`, replace hard-coded layout URL construction with `graphUrl()`.
- Omit hidden capabilities entirely; render neither disabled controls nor explanatory empty cards.

- [ ] **Step 5: Add component assertions for hidden unsupported controls**

Extend the nearest existing upstream tests (`GraphTab.deadcode.test.tsx`, `StatsTab.test.tsx`, `NodeDetailPanel.test.tsx`) with an explicit all-false capability fixture. Assert that dead-code filters, ADR actions, delete/browse actions, and source-code lookup are absent using `queryByRole`/`queryByText`. Keep existing all-enabled upstream assertions unchanged.

- [ ] **Step 6: Update provenance patch inventory**

Replace the empty patch statement in `PROVENANCE.md` with:

```markdown
1. `src/lib/kgAdapter.ts` and `src/lib/kgAdapter.test.ts`: select live/static kg transports and parse capabilities fail-closed.
2. `src/App.tsx`, `src/components/{GraphTab,StatsTab,ControlTab,NodeDetailPanel}.tsx`, and `src/hooks/useGraphData.ts`: route data through the adapter and hide features without kg semantics.
3. Build configuration changes listed below: produce self-contained relative assets for wheel and Pages deployment.
```

- [ ] **Step 7: Run all frontend tests and build**

Run:

```bash
cd ui/graph-ui
npm test -- --run
npm run build
```

Expected: all pinned upstream and kg adapter tests pass; Vite build succeeds.

- [ ] **Step 8: Commit**

```bash
git add ui/graph-ui
git commit -m "feat(ui): adapt pinned CBM shell to kg capabilities" \
  -m "Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

### Task 7: Build one static snapshot pipeline and package the UI

**Files:**
- Create: `scripts/build_graph_ui.py`
- Create: `tests/viz/test_snapshot.py`
- Create: `tests/viz/test_package.py`
- Modify: `ui/graph-ui/vite.config.ts`
- Modify: `pyproject.toml` only if the wheel test proves Hatch excludes `src/kg/viz/assets/`
- Modify: `Makefile`
- Modify: `.github/workflows/pages.yml`
- Modify: `.github/workflows/test.yml`
- Move: `docs/demo/graph.json` to `ui/graph-ui/snapshots/demo-source.json`
- Move: `docs/demo/graph-stress.json` to `ui/graph-ui/snapshots/stress-source.json`
- Modify: `bench/export_demo_graph.py`
- Modify: `bench/gen_stress_graph.py`

**Interfaces:**
- Consumes: old demo source JSON, `build_layout_payload`-equivalent pure normalization helper from Task 3, npm project from Tasks 1/6.
- Produces: `python scripts/build_graph_ui.py --snapshot <source> --output <dir>`, `src/kg/viz/assets/index.html`, hashed local assets, `graph-snapshot.json`, `capabilities.json`; wheel containing all files.
- Guarantees: static/live payload equality for identical normalized input; relative assets; no external runtime URLs.

- [ ] **Step 1: Write failing snapshot equivalence test**

Create `tests/viz/test_snapshot.py`:

```python
import json

from kg.viz.api import build_layout_payload, build_snapshot_payload
from kg.storage.sqlite import SQLiteAdapter


def test_static_snapshot_equals_live_payload(seeded_adapter: SQLiteAdapter) -> None:
    live = build_layout_payload(seeded_adapter)
    static = build_snapshot_payload(seeded_adapter)
    assert static == live
    assert json.dumps(static, sort_keys=True, separators=(",", ":"), allow_nan=False)
```

If no shared `seeded_adapter` fixture exists, move the Task 3 seed helper into `tests/viz/conftest.py` and use it from both test modules.

- [ ] **Step 2: Write failing package-resource test**

Create `tests/viz/test_package.py`:

```python
from importlib.resources import files


def test_built_graph_ui_assets_are_packaged() -> None:
    root = files("kg.viz").joinpath("assets")
    assert root.joinpath("index.html").is_file()
    assert root.joinpath("graph-snapshot.json").is_file()
    assert root.joinpath("capabilities.json").is_file()
    html = root.joinpath("index.html").read_text(encoding="utf-8")
    assert "https://" not in html
```

- [ ] **Step 3: Run tests to verify failure**

Run: `uv run pytest tests/viz/test_snapshot.py tests/viz/test_package.py -q`

Expected: snapshot helper or generated packaged assets are absent.

- [ ] **Step 4: Expose one snapshot serializer**

In `src/kg/viz/api.py`, implement:

```python
def build_snapshot_payload(adapter: StorageAdapter) -> dict[str, object]:
    return build_layout_payload(adapter, max_nodes=_MAX_NODES, max_edges=_MAX_EDGES)
```

For source JSON import, add a private validated loader in `scripts/build_graph_ui.py` that converts the retained `{nodes,edges}` shape into temporary `LayoutNodeInput`/`LayoutEdgeInput`, then calls the same private normalization/layout serialization helper used by `build_layout_payload`. Do not duplicate coordinate or color logic in the script.

- [ ] **Step 5: Make Vite output deployable at any base path**

Set `base: "./"` in `ui/graph-ui/vite.config.ts`. Keep upstream plugins and test config unchanged. Verify generated `index.html` references `./assets/...` and contains no CDN URL.

- [ ] **Step 6: Implement the build script**

Create `scripts/build_graph_ui.py` using `argparse`, `json`, `pathlib`, `shutil`, and `subprocess.run([...], check=True)` only. Defaults:

- source: `ui/graph-ui/snapshots/demo-source.json`;
- Vite dist: `ui/graph-ui/dist`;
- package output: `src/kg/viz/assets`.

Sequence:

1. validate source JSON object and bounded node/edge lists;
2. generate byte-stable `graph-snapshot.json` through the shared API/layout code;
3. write static `capabilities.json` equal to `build_capabilities(static=True)`;
4. run `npm ci` unless `--skip-install`;
5. run `npm test -- --run` unless `--skip-tests`;
6. run `npm run build`;
7. replace `src/kg/viz/assets` atomically via sibling temporary directory;
8. copy the snapshot and capabilities beside `index.html`;
9. scan text build outputs for `https://`, `http://`, and protocol-relative `//` runtime asset references; permit only visible attribution links already present in copy, not script/style/font/image imports.

Support `--output` for Pages staging and `--skip-install`, `--skip-tests` for CI phases that already ran them.

- [ ] **Step 7: Move datasets and retarget generators**

```bash
mkdir -p ui/graph-ui/snapshots
git mv docs/demo/graph.json ui/graph-ui/snapshots/demo-source.json
git mv docs/demo/graph-stress.json ui/graph-ui/snapshots/stress-source.json
```

Update `bench/export_demo_graph.py` and `bench/gen_stress_graph.py` output paths to these exact files. Keep their data schema unchanged; the build script is the compatibility boundary.

- [ ] **Step 8: Generate assets and run focused tests**

Run:

```bash
uv run python scripts/build_graph_ui.py
uv run pytest tests/viz/test_snapshot.py tests/viz/test_package.py tests/viz/test_server_api.py -q
```

Expected: all tests pass and `src/kg/viz/assets/` contains self-contained build output.

- [ ] **Step 9: Prove wheel inclusion before changing Hatch config**

Run:

```bash
rm -rf dist
uv build
python3 - <<'PY'
from pathlib import Path
from zipfile import ZipFile
wheel = next(Path("dist").glob("*.whl"))
with ZipFile(wheel) as zf:
    names = zf.namelist()
assert any(name.endswith("kg/viz/assets/index.html") for name in names)
assert any(name.endswith("kg/viz/assets/graph-snapshot.json") for name in names)
print("wheel UI assets: pass")
PY
```

Expected: pass. Only if this fails, add the minimal Hatch force-include/package-data mapping required by the installed Hatch version, then repeat the check.

- [ ] **Step 10: Add build targets**

Add to `Makefile`:

```make
ui-test:
	cd ui/graph-ui && npm ci && npm test -- --run

ui-build:
	uv run python scripts/build_graph_ui.py

snapshot:
	uv run python scripts/build_graph_ui.py --skip-install --skip-tests
```

- [ ] **Step 11: Retarget Pages workflow**

In `.github/workflows/pages.yml`:

- cache npm using `ui/graph-ui/package-lock.json`;
- install Python and uv using the same versions/actions as `.github/workflows/test.yml`;
- run `uv sync --frozen`;
- run `uv run python scripts/build_graph_ui.py --output .pages-dist`;
- upload `.pages-dist` as the Pages artifact;
- retarget path filters to `ui/graph-ui/**`, `src/kg/viz/{api,layout3d}.py`, `scripts/build_graph_ui.py`, retained snapshot inputs, and the workflow itself.

- [ ] **Step 12: Add frontend verification to CI**

In `.github/workflows/test.yml`, add one Linux job after dependency setup:

```yaml
- name: Test pinned CBM graph UI
  working-directory: ui/graph-ui
  run: npm ci && npm test -- --run && npm run build
- name: Generate packaged graph UI
  run: uv run python scripts/build_graph_ui.py --skip-install --skip-tests
- name: Verify packaged assets
  run: uv run pytest tests/viz/test_package.py tests/viz/test_snapshot.py -q
```

Do not duplicate this npm job across the OS matrix.

- [ ] **Step 13: Commit**

```bash
git add scripts/build_graph_ui.py src/kg/viz/api.py src/kg/viz/assets ui/graph-ui pyproject.toml Makefile .github/workflows/pages.yml .github/workflows/test.yml bench tests/viz
git commit -m "build(viz): package shared live and static UI" \
  -m "Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

### Task 8: Remove duplicate graph apps, document provenance, and verify end to end

**Files:**
- Delete: `docs/demo/src/**`
- Delete: `docs/demo/index.html`
- Delete: `docs/demo/package.json`
- Delete: `docs/demo/package-lock.json`
- Delete: `docs/demo/tsconfig.json`
- Delete: `docs/demo/vite.config.ts`
- Delete: `docs/demo/scripts/**`
- Delete: `docs/demo/.gitignore`
- Remove directory: `docs/demo/` after retained datasets have moved
- Modify: `README.md`
- Modify: `CHANGELOG.md`
- Modify: relevant graph architecture document discovered under `docs/architecture/`; if none describes visualization, update only README and CHANGELOG
- Test: full Python, frontend, wheel, and browser smoke checks

**Interfaces:**
- Consumes: completed live/static implementation from Tasks 1–7.
- Produces: one graph frontend, current documentation, green full verification, clean tracked diff.

- [ ] **Step 1: Delete the obsolete frontend only after the replacement passes**

Run:

```bash
rm -rf docs/demo/src docs/demo/scripts
rm -f docs/demo/index.html docs/demo/package.json docs/demo/package-lock.json \
  docs/demo/tsconfig.json docs/demo/vite.config.ts docs/demo/.gitignore
rmdir docs/demo 2>/dev/null || true
```

Expected: `docs/demo/` is absent; both datasets already exist under `ui/graph-ui/snapshots/`.

- [ ] **Step 2: Update user-facing documentation**

In `README.md`, replace references to the old Pages demo/2D renderer with:

- `kg viz` launches the pinned CBM 3D shell at `http://127.0.0.1:9749` by default;
- the wheel includes frontend assets and needs no Node/npm at runtime;
- GitHub Pages uses the same build with a bundled read-only snapshot;
- upstream attribution links to repository and full commit.

In `CHANGELOG.md`, add one entry under the current unreleased section covering the shared pinned UI, deterministic Python layout, live/static parity, and retired duplicate demo. Update an existing visualization architecture document only when one already owns this subject.

- [ ] **Step 3: Run Python formatting/static checks used by this repository**

Inspect `Makefile` and CI for the exact existing lint command. Run that command. Then run:

```bash
uv run pytest tests/viz tests/test_viz_cache.py -q
```

Expected: all visualization tests pass; no skipped tests introduced by this plan.

- [ ] **Step 4: Run the full Python suite**

Run: `uv run pytest -p no:cacheprovider`

Expected: full suite passes.

- [ ] **Step 5: Run frontend and package verification**

Run:

```bash
cd ui/graph-ui && npm ci && npm test -- --run && npm run build
cd ../..
uv run python scripts/build_graph_ui.py --skip-install --skip-tests
rm -rf dist && uv build
uv tool install --force dist/*.whl
```

Expected: frontend tests/build pass, assets regenerate without diff, wheel builds and installs.

- [ ] **Step 6: Run live browser smoke check**

Launch installed `kg viz` against a temporary seeded project at a non-default free port. Verify at desktop viewport:

1. pinned CBM top shell, sidebar, filters, graph canvas, display controls, labels, tooltip, and node selection render;
2. Projects and Control appear only when the live capability response explicitly enables their backed behavior;
3. ADR, dead-code, missed-call, code-view, browse, and deletion controls are absent;
4. refresh preserves valid graph state and `/api/layout` returns matching ETag/304 behavior;
5. browser network panel shows no external runtime requests;
6. server listens on `127.0.0.1`, not `0.0.0.0`.

Capture one screenshot for local review; do not commit it unless the repository already tracks release screenshots.

- [ ] **Step 7: Run static browser smoke check**

Serve the generated Pages directory with `python3 -m http.server` bound to `127.0.0.1`. Verify:

1. the same shell and graph geometry render;
2. `graph-snapshot.json` is the graph source;
3. mutation controls are absent;
4. no request targets `/api/index`, `/rpc`, or an external host;
5. selected node positions match the live `/api/layout` payload for the same demo source.

Stop both local servers after verification.

- [ ] **Step 8: Check provenance and repository cleanliness**

Run:

```bash
git diff --check
git status --short
git diff -- ui/graph-ui/PROVENANCE.md README.md CHANGELOG.md
```

Expected: only intended task files plus the user's pre-existing `.planning/` and `.pi/` changes appear; provenance lists every local frontend patch.

- [ ] **Step 9: Commit documentation and retirement**

```bash
git add -A docs/demo README.md CHANGELOG.md docs/architecture ui/graph-ui/PROVENANCE.md
git commit -m "docs(viz): retire duplicate graph demo" \
  -m "Co-Authored-By: Claude <noreply@anthropic.com>"
```

- [ ] **Step 10: Final acceptance check**

Run:

```bash
git log -8 --oneline
uv run pytest -p no:cacheprovider
cd ui/graph-ui && npm test -- --run && npm run build
```

Expected: all acceptance criteria in `docs/superpowers/specs/2026-08-02-cbm-3d-graph-ui-design.md` are demonstrated; report exact test counts and any intentionally hidden capabilities.
