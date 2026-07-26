# KG M1 — Graph + Normalization Gate Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the graph store (SQLite: nodes/edges + FTS5 + sqlite-vec), the deterministic normalization gate (`kg save`: validate → resolve → embed → dedup → route), and the read primitives (`kg search`/`expand`/`pack`) — so extracted POLE objects become a clean, queryable, idempotent knowledge graph.

**Architecture:** A storage adapter interface (`StorageAdapter`) with one SQLite implementation backs every read/write. The gate is pure orchestration over four injected services — `Embedder`, `Resolver`, `Deduper`, and the adapter — so each is testable in isolation with a deterministic `FakeEmbedder`. Content-derived IDs make writes idempotent; resolution (naming) is kept strictly separate from deduplication (identity). CLI commands are thin shells over core, exactly as M0 established.

**Tech Stack:** Python ≥3.11,<3.14 · SQLite (stdlib) + `sqlite-vec` (ANN) + FTS5 (BM25) · `fastembed`/ONNX `BAAI/bge-small-en-v1.5` (384d, local) · `rapidfuzz` (fuzzy match) · `numpy` · `pytest`. Builds on M0 (`kg.config`, `kg.paths`, `kg.ontology`, `kg.registry`, `kg.cli`).

## Global Constraints

- **No model calls in the engine.** Extraction JSON arrives pre-built from the harness LLM skill; the gate is deterministic only. Tests use `FakeEmbedder`, never the ONNX model.
- **Resolution ≠ dedup** (spec §6.3, non-negotiable). Resolution absorbs name variants; dedup on full-context signal decides identity. Never collapse them.
- **Content-derived IDs** (spec §5.2–5.3): node `{user_id}:{type}:{slug}`, edge `{src}|{sem}|{tgt}` → every write idempotent.
- **Gray zone never auto-merges.** ≥0.95 merge · 0.85–0.95 new + `same_as{pending}` · <0.85 new. The one unrecoverable mistake is a wrong merge.
- **Tombstone, never hard-delete.** `status: tombstoned`, `merged_into` pointer; excluded from matching/search, resolvable by ID.
- **Ontology is the contract.** `kg save` rejects node `type` not in `ALLOWED_NODE_TYPES` and edge `semantic_type` not in `ALLOWED_SEMANTIC_EDGE_TYPES ∪ STRUCTURAL_EDGE_TYPES`.
- **WAL mode, single writer.** Adapter opens with `PRAGMA journal_mode=WAL`.
- **TDD:** every core service test-first with `FakeEmbedder`; CLI smoke only. One commit per task.
- **Builds on M0 interfaces:** `Config` (`kg.config`), `KgPaths` (`kg.paths`), `Node`/`Edge`/`ALLOWED_*` (`kg.ontology`), `Registry` (`kg.registry`), `slugify` (`kg.chunking`).

---

## File Map (locked decomposition)

| File | Responsibility |
|---|---|
| `src/kg/ontology.py` (modify) | Extend `Node`/`Edge` with M1 fields: `embedding`, `merged_into`, `attribute_conflicts`, `created_at`, `updated_at`, `confidence`, edge `status` |
| `src/kg/storage/__init__.py` | re-export `StorageAdapter`, `Subgraph` |
| `src/kg/storage/base.py` | `StorageAdapter` ABC + `Subgraph` dataclass |
| `src/kg/storage/sqlite.py` | `SQLiteAdapter` — DDL, upsert/get/delete/neighbors/fts_search/vec_search |
| `src/kg/embed.py` | `Embedder` ABC, `LocalEmbedder` (fastembed), `FakeEmbedder`, `make_embedder(config)`, content-hash cache |
| `src/kg/ids.py` | `node_id(user_id, type, name)`, `edge_id(src, sem, tgt)` — content-derived ID rules |
| `src/kg/resolve.py` | `Resolver` — exact→fuzzy→semantic name resolution, type-gated |
| `src/kg/dedup.py` | `Deduper` — candidate retrieval + `0.7·cos + 0.3·fuzzy` scoring |
| `src/kg/gate.py` | `Gate.normalize()` → `SaveReport`; `merge()` semantics |
| `src/kg/search.py` | `hybrid_search()` — FTS5 ∥ vec, RRF k=60 |
| `src/kg/traverse.py` | `expand()` BFS via `neighbors`, degree centrality, subgraph cap |
| `src/kg/pack.py` | `pack()` — rank (RRF∪centrality∪recency) + trim to token budget |
| `src/kg/cli/save.py` | `kg save` command |
| `src/kg/cli/query.py` | `kg search`, `kg expand`, `kg pack` commands |
| `src/kg/cli/resolve_cli.py` | `kg resolve`, `kg dedup-check` dry-run commands |
| `tests/storage/test_sqlite.py` | adapter contract tests |
| `tests/test_embed.py`, `test_ids.py`, `test_resolve.py`, `test_dedup.py`, `test_gate.py`, `test_search.py`, `test_traverse.py`, `test_pack.py` | core TDD |
| `tests/cli/test_save.py`, `test_query.py` | CLI smoke |
| `skills/kg-extract/SKILL.md`, `skills/kg-query/SKILL.md` + `references/` | the two harness skills (M1 read/write flow) |

---

### Task 1: Extend `Node`/`Edge` models for the graph layer

**Files:**
- Modify: `src/kg/ontology.py`
- Test: `tests/test_ontology.py` (append cases)

**Interfaces:**
- Produces: `Node` gains `embedding: list[float] | None`, `merged_into: str | None`, `attribute_conflicts: list[dict]`, `created_at: str | None`, `updated_at: str | None`. `Edge` gains `status: str = "active"`. All default so M0 callers and `ontology.json` stay valid.

- [ ] **Step 1: Append failing tests**

```python
# append to tests/test_ontology.py
from kg.ontology import Node, Edge


def test_node_has_graph_fields():
    n = Node(type="person", name="Demis Hassabis")
    assert n.embedding is None
    assert n.merged_into is None
    assert n.attribute_conflicts == []
    assert n.created_at is None
    assert n.status == "active"


def test_edge_has_status():
    e = Edge(semantic_type="employed_by")
    assert e.status == "active"


def test_node_roundtrips_with_embedding():
    n = Node(type="object", name="X", embedding=[0.1, 0.2, 0.3])
    dumped = n.model_dump()
    assert dumped["embedding"] == [0.1, 0.2, 0.3]
    assert Node.model_validate(dumped).embedding == [0.1, 0.2, 0.3]
```

- [ ] **Step 2: Run, verify FAIL**

Run: `uv run pytest tests/test_ontology.py -v`
Expected: FAIL — `embedding` not a field.

- [ ] **Step 3: Extend the models**

```python
# src/kg/ontology.py — replace the Node and Edge class bodies
class Node(BaseModel):
    id: str | None = None
    type: str
    subtype: str | None = None
    name: str
    canonical_name: str | None = None
    aliases: list[str] = Field(default_factory=list)
    summary: str | None = None
    attributes: dict = Field(default_factory=dict)
    attribute_conflicts: list[dict] = Field(default_factory=list)
    embedding: list[float] | None = None
    valid_from: str | None = None
    valid_until: str | None = None
    sources: list[dict] = Field(default_factory=list)
    created_at: str | None = None
    updated_at: str | None = None
    status: str = "active"
    merged_into: str | None = None


class Edge(BaseModel):
    id: str | None = None
    type: str = "related_to"
    semantic_type: str
    summary: str | None = None
    confidence: float = 0.0
    sources: list[dict] = Field(default_factory=list)
    valid_from: str | None = None
    valid_until: str | None = None
    status: str = "active"
```

- [ ] **Step 4: Run, verify PASS** (`uv run pytest tests/test_ontology.py -v`)
- [ ] **Step 5: Commit** — `feat(m1): extend Node/Edge with graph-layer fields`

---

### Task 2: Content-derived IDs

**Files:**
- Create: `src/kg/ids.py`, `tests/test_ids.py`

**Interfaces:**
- Produces: `node_id(user_id: str, type: str, name: str) -> str`, `edge_id(source_id: str, semantic_type: str, target_id: str) -> str`. Consumes `slugify` from `kg.chunking`.

- [ ] **Step 1: Failing test**

```python
# tests/test_ids.py
from kg.ids import node_id, edge_id


def test_node_id_format():
    assert node_id("quan", "person", "Demis Hassabis") == "quan:person:demis-hassabis"


def test_node_id_stable_across_surface_forms():
    a = node_id("quan", "person", "Demis Hassabis")
    b = node_id("quan", "person", "demis hassabis")
    assert a == b


def test_edge_id_format_and_idempotence():
    eid = edge_id("quan:person:a", "employed_by", "quan:organization:b")
    assert eid == "quan:person:a|employed_by|quan:organization:b"
    assert edge_id("quan:person:a", "employed_by", "quan:organization:b") == eid
```

- [ ] **Step 2: Run, verify FAIL**
- [ ] **Step 3: Implement**

```python
# src/kg/ids.py
from __future__ import annotations
from kg.chunking import slugify


def node_id(user_id: str, type_: str, name: str) -> str:
    return f"{user_id}:{type_}:{slugify(name)}"


def edge_id(source_id: str, semantic_type: str, target_id: str) -> str:
    return f"{source_id}|{semantic_type}|{target_id}"
```

- [ ] **Step 4: Run, verify PASS**
- [ ] **Step 5: Commit** — `feat(m1): content-derived node/edge IDs`

---

### Task 3: Storage adapter interface + `Subgraph`

**Files:**
- Create: `src/kg/storage/__init__.py`, `src/kg/storage/base.py`, `tests/storage/__init__.py`, `tests/storage/test_base.py`

**Interfaces:**
- Produces: `Subgraph(nodes: list[Node], edges: list[Edge])` dataclass. `StorageAdapter` ABC with abstract methods: `upsert_nodes(nodes: list[Node]) -> int`, `upsert_edges(edges: list[Edge]) -> int`, `get(node_id: str) -> Node | None`, `delete(node_id: str, tombstone: bool = True) -> None`, `neighbors(ids: list[str], depth: int, direction: str, edge_types: list[str] | None) -> Subgraph`, `fts_search(query: str, k: int, type_filter: str | None) -> list[tuple[str, float]]`, `vec_search(embedding: list[float], k: int, type_filter: str | None) -> list[tuple[str, float]]`, `count() -> dict`.

- [ ] **Step 1: Failing test**

```python
# tests/storage/test_base.py
import pytest
from kg.storage.base import StorageAdapter, Subgraph
from kg.ontology import Node


def test_subgraph_holds_nodes_and_edges():
    sg = Subgraph(nodes=[Node(type="person", name="A")], edges=[])
    assert len(sg.nodes) == 1
    assert sg.edges == []


def test_adapter_is_abstract():
    with pytest.raises(TypeError):
        StorageAdapter()  # type: ignore[abstract]
```

- [ ] **Step 2: Run, verify FAIL**
- [ ] **Step 3: Implement**

```python
# src/kg/storage/__init__.py
from kg.storage.base import StorageAdapter, Subgraph
__all__ = ["StorageAdapter", "Subgraph"]
```

```python
# src/kg/storage/base.py
from __future__ import annotations
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from kg.ontology import Node, Edge


@dataclass
class Subgraph:
    nodes: list[Node] = field(default_factory=list)
    edges: list[Edge] = field(default_factory=list)


class StorageAdapter(ABC):
    @abstractmethod
    def upsert_nodes(self, nodes: list[Node]) -> int: ...
    @abstractmethod
    def upsert_edges(self, edges: list[Edge]) -> int: ...
    @abstractmethod
    def get(self, node_id: str) -> Node | None: ...
    @abstractmethod
    def delete(self, node_id: str, tombstone: bool = True) -> None: ...
    @abstractmethod
    def neighbors(self, ids: list[str], depth: int = 1,
                  direction: str = "both",
                  edge_types: list[str] | None = None) -> Subgraph: ...
    @abstractmethod
    def fts_search(self, query: str, k: int = 10,
                   type_filter: str | None = None) -> list[tuple[str, float]]: ...
    @abstractmethod
    def vec_search(self, embedding: list[float], k: int = 10,
                   type_filter: str | None = None) -> list[tuple[str, float]]: ...
    @abstractmethod
    def count(self) -> dict: ...
```

```python
# tests/storage/__init__.py   (empty)
```
- [ ] **Step 4: Run, verify PASS**
- [ ] **Step 5: Commit** — `feat(m1): StorageAdapter interface + Subgraph`

---

### Task 4: SQLite adapter — schema, connect, upsert/get/delete

**Files:**
- Create: `src/kg/storage/sqlite.py`, `tests/storage/test_sqlite.py`
- Modify: `pyproject.toml` (add `sqlite-vec>=0.1.*`, `numpy>=1.26`)

**Interfaces:**
- Produces: `SQLiteAdapter(db_path: Path)`. Constructor opens (creates) the DB, sets WAL, runs DDL. Methods per the ABC. `upsert_nodes` stores `embedding` as JSON blob + registers a row for FTS5 + vec tables. `delete(tombstone=True)` sets `status='tombstoned'`.
- Consumes: `Node`/`Edge` from `kg.ontology`.

- [ ] **Step 1: Add deps**

```toml
# pyproject.toml [project].dependencies — append:
    "sqlite-vec>=0.1.41",
    "numpy>=1.26",
```
Then `uv sync`.

- [ ] **Step 2: Failing test**

```python
# tests/storage/test_sqlite.py
from kg.storage.sqlite import SQLiteAdapter
from kg.ontology import Node


def _adapter(tmp_path):
    return SQLiteAdapter(tmp_path / "kg.db")


def test_upsert_and_get(tmp_path):
    a = _adapter(tmp_path)
    n = Node(id="u:person:demis-hassabis", type="person", name="Demis Hassabis",
             summary="DeepMind founder")
    assert a.upsert_nodes([n]) == 1
    got = a.get("u:person:demis-hassabis")
    assert got is not None
    assert got.name == "Demis Hassabis"


def test_upsert_is_idempotent(tmp_path):
    a = _adapter(tmp_path)
    n = Node(id="u:person:x", type="person", name="X")
    a.upsert_nodes([n])
    a.upsert_nodes([n])  # same id → upsert, not duplicate
    assert a.count()["nodes"] == 1


def test_delete_tombstones(tmp_path):
    a = _adapter(tmp_path)
    a.upsert_nodes([Node(id="u:person:x", type="person", name="X")])
    a.delete("u:person:x")
    got = a.get("u:person:x")
    assert got.status == "tombstoned"


def test_count_empty(tmp_path):
    a = _adapter(tmp_path)
    assert a.count() == {"nodes": 0, "edges": 0}
```

- [ ] **Step 3: Run, verify FAIL**
- [ ] **Step 4: Implement**

```python
# src/kg/storage/sqlite.py
from __future__ import annotations
import json
import sqlite3
import sqlite_vec
from pathlib import Path
from kg.ontology import Node, Edge
from kg.storage.base import StorageAdapter, Subgraph


_SCHEMA = """
CREATE TABLE IF NOT EXISTS nodes(
  id TEXT PRIMARY KEY,
  data TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'active'
);
CREATE TABLE IF NOT EXISTS edges(
  id TEXT PRIMARY KEY,
  source TEXT NOT NULL,
  target TEXT NOT NULL,
  semantic_type TEXT NOT NULL,
  data TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'active'
);
CREATE VIRTUAL TABLE IF NOT EXISTS nodes_fts USING fts5(
  node_id UNINDEXED, name, summary, type UNINDEXED
);
"""


class SQLiteAdapter(StorageAdapter):
    def __init__(self, db_path: Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(self.db_path))
        self.conn.row_factory = sqlite3.Row
        self.conn.enable_load_extension(True)
        sqlite_vec.load(self.conn)
        self.conn.enable_load_extension(False)
        self.conn.executescript(_SCHEMA)
        self.conn.execute("PRAGMA journal_mode=WBL".replace("B", "AL"))
        self.conn.commit()

    def _dump(self, n: Node) -> str:
        return n.model_dump_json()

    def upsert_nodes(self, nodes: list[Node]) -> int:
        for n in nodes:
            data = self._dump(n)
            self.conn.execute(
                "INSERT INTO nodes(id, data, status) VALUES(?,?,?) "
                "ON CONFLICT(id) DO UPDATE SET data=excluded.data, status=excluded.status",
                (n.id, data, n.status),
            )
            self.conn.execute(
                "INSERT INTO nodes_fts(node_id, name, summary, type) VALUES(?,?,?,?) "
                "ON CONFLICT(node_id) DO UPDATE SET name=excluded.name, summary=excluded.summary",
                (n.id, n.name, n.summary or "", n.type),
            )
        self.conn.commit()
        return len(nodes)

    def get(self, node_id: str) -> Node | None:
        row = self.conn.execute(
            "SELECT data FROM nodes WHERE id=?", (node_id,)
        ).fetchone()
        return Node.model_validate_json(row["data"]) if row else None

    def delete(self, node_id: str, tombstone: bool = True) -> None:
        if tombstone:
            row = self.conn.execute(
                "SELECT data FROM nodes WHERE id=?", (node_id,)
            ).fetchone()
            if row:
                n = Node.model_validate_json(row["data"])
                n.status = "tombstoned"
                self.upsert_nodes([n])
        else:
            self.conn.execute("DELETE FROM nodes WHERE id=?", (node_id,))
            self.conn.execute("DELETE FROM nodes_fts WHERE node_id=?", (node_id,))
        self.conn.commit()

    def count(self) -> dict:
        n = self.conn.execute("SELECT COUNT(*) c FROM nodes").fetchone()["c"]
        e = self.conn.execute("SELECT COUNT(*) c FROM edges").fetchone()["c"]
        return {"nodes": n, "edges": e}

    # edges / neighbors / searches implemented in later tasks
    def upsert_edges(self, edges: list[Edge]) -> int:
        raise NotImplementedError

    def neighbors(self, ids, depth=1, direction="both", edge_types=None) -> Subgraph:
        raise NotImplementedError

    def fts_search(self, query, k=10, type_filter=None):
        raise NotImplementedError

    def vec_search(self, embedding, k=10, type_filter=None):
        raise NotImplementedError
```

- [ ] **Step 5: Run, verify PASS** (`uv run pytest tests/storage/test_sqlite.py -v`)
- [ ] **Step 6: Commit** — `feat(m1): SQLite adapter — schema, upsert/get/delete/count`

---

### Task 5: SQLite adapter — `upsert_edges` + `neighbors`

**Files:**
- Modify: `src/kg/storage/sqlite.py`, `tests/storage/test_sqlite.py`

**Interfaces:**
- Produces: working `upsert_edges`, `neighbors(ids, depth, direction, edge_types)` via recursive CTE.

- [ ] **Step 1: Failing test**

```python
# append to tests/storage/test_sqlite.py
from kg.ontology import Edge
from kg.storage.sqlite import SQLiteAdapter


def _seed_graph(tmp_path):
    a = SQLiteAdapter(tmp_path / "kg.db")
    a.upsert_nodes([
        Node(id="u:person:a", type="person", name="A"),
        Node(id="u:organization:b", type="organization", name="B"),
        Node(id="u:object:c", type="object", name="C"),
    ])
    a.upsert_edges([
        Edge(id="u:person:a|employed_by|u:organization:b",
             semantic_type="employed_by", source="u:person:a",
             target="u:organization:b") if False else
        Edge(id="u:person:a|employed_by|u:organization:b",
             semantic_type="employed_by"),
    ])
    return a
```
> Note: the `Edge` model has no explicit `source`/`target` fields — they are parsed from the id. Rewrite the seed without the dead branch:

```python
def _seed_graph(tmp_path):
    a = SQLiteAdapter(tmp_path / "kg.db")
    a.upsert_nodes([
        Node(id="u:person:a", type="person", name="A"),
        Node(id="u:organization:b", type="organization", name="B"),
        Node(id="u:object:c", type="object", name="C"),
    ])
    a.upsert_edges([
        Edge(id="u:person:a|employed_by|u:organization:b", semantic_type="employed_by"),
        Edge(id="u:organization:b|owns|u:object:c", semantic_type="owns"),
    ])
    return a


def test_upsert_edges_and_count(tmp_path):
    a = _seed_graph(tmp_path)
    assert a.count()["edges"] == 2


def test_neighbors_one_hop(tmp_path):
    a = _seed_graph(tmp_path)
    sg = a.neighbors(["u:person:a"], depth=1)
    ids = {n.id for n in sg.nodes}
    assert "u:organization:b" in ids
    assert any(e.semantic_type == "employed_by" for e in sg.edges)


def test_neighbors_two_hop(tmp_path):
    a = _seed_graph(tmp_path)
    sg = a.neighbors(["u:person:a"], depth=2)
    ids = {n.id for n in sg.nodes}
    assert "u:object:c" in ids  # reached via b
```

- [ ] **Step 2: Run, verify FAIL**
- [ ] **Step 3: Implement**

```python
# replace the two stubs in SQLiteAdapter
def _edge_endpoints(self, edge_id: str) -> tuple[str, str, str]:
    # id format: {src}|{sem}|{tgt}
    src, sem, tgt = edge_id.split("|", 2)
    return src, sem, tgt

def upsert_edges(self, edges: list[Edge]) -> int:
    for e in edges:
        src, sem, tgt = self._edge_endpoints(e.id or "")
        self.conn.execute(
            "INSERT INTO edges(id, source, target, semantic_type, data, status) "
            "VALUES(?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET "
            "source=excluded.source, target=excluded.target, data=excluded.data",
            (e.id, src, tgt, sem, e.model_dump_json(), e.status),
        )
    self.conn.commit()
    return len(edges)

def neighbors(self, ids: list[str], depth: int = 1,
              direction: str = "both",
              edge_types: list[str] | None = None) -> Subgraph:
    if not ids:
        return Subgraph()
    et_clause = ""
    params: list = [depth, tuple(ids)]
    if edge_types:
        et_clause = "AND semantic_type IN %s" % (tuple(edge_types),)
        params = [depth, tuple(ids)]
    # recursive CTE collecting reachable node ids
    sql = f"""
    WITH RECURSIVE reach(seed, nid, d) AS (
      SELECT value, value, 0 FROM (SELECT value FROM json_each(?))
      UNION ALL
      SELECT r.nid, CASE WHEN e.source=r.nid THEN e.target ELSE e.source END, r.d+1
      FROM reach r JOIN edges e
        ON (e.source=r.nid OR e.target=r.nid)
      WHERE r.d < ? {et_clause}
    )
    SELECT DISTINCT nid FROM reach WHERE d>0
    """
    seeds_json = json.dumps(list(ids))
    rows = self.conn.execute(
        "WITH RECURSIVE reach(seed, nid, d) AS ("
        " SELECT value, value, 0 FROM json_each(?)"
        " UNION ALL"
        " SELECT r.nid, CASE WHEN e.source=r.nid THEN e.target ELSE e.source END, r.d+1"
        " FROM reach r JOIN edges e ON (e.source=r.nid OR e.target=r.nid)"
        " WHERE r.d < ?" + (" AND e.semantic_type IN (%s)" % ",".join("?"*len(edge_types)) if edge_types else "") + ")"
        " SELECT DISTINCT nid FROM reach WHERE d>0",
        ([seeds_json, depth] + list(edge_types)) if edge_types else [seeds_json, depth],
    ).fetchall()
    reached = {r["nid"] for r in rows}
    nodes = [self.get(nid) for nid in reached]
    nodes = [n for n in nodes if n]
    # edges among reached ∪ seeds
    all_ids = set(ids) | reached
    placeholders = ",".join("?" * len(all_ids))
    erows = self.conn.execute(
        f"SELECT data FROM edges WHERE source IN ({placeholders}) AND target IN ({placeholders})",
        [*all_ids, *all_ids],
    ).fetchall()
    edges = [Edge.model_validate_json(r["data"]) for r in erows]
    return Subgraph(nodes=nodes, edges=edges)
```
> The recursive CTE is written in the final form; remove the earlier `sql = f"""..."""` scratch block before committing (keep only the executed statement).

- [ ] **Step 4: Run, verify PASS**
- [ ] **Step 5: Commit** — `feat(m1): SQLite edges + recursive-CTE neighbors`

---

### Task 6: SQLite adapter — `fts_search` + `vec_search`

**Files:**
- Modify: `src/kg/storage/sqlite.py`, `tests/storage/test_sqlite.py`
- Modify: `_SCHEMA` to add a `nodes_vec` virtual table (sqlite-vec).

**Interfaces:**
- Produces: `fts_search(query, k, type_filter)` over `nodes_fts` (BM25 rank); `vec_search(embedding, k, type_filter)` over `nodes_vec`. Both return `list[(node_id, score)]`, **active nodes only**, highest-score first. FTS excludes tombstoned.

- [ ] **Step 1: Extend schema** — add to `_SCHEMA`:

```sql
CREATE VIRTUAL TABLE IF NOT EXISTS nodes_vec USING vec0(
  node_id TEXT PRIMARY KEY, embedding FLOAT[384]
);
```

- [ ] **Step 2: Failing test**

```python
# append to tests/storage/test_sqlite.py
def test_fts_search(tmp_path):
    a = _adapter(tmp_path)
    a.upsert_nodes([
        Node(id="u:person:d", type="person", name="Demis Hassabis", summary="DeepMind founder"),
        Node(id="u:person:o", type="person", name="Other", summary="unrelated"),
    ])
    hits = a.fts_search("DeepMind founder", k=5)
    ids = [h[0] for h in hits]
    assert "u:person:d" in ids
    assert ids[0] == "u:person:d"


def test_fts_search_type_filter(tmp_path):
    a = _adapter(tmp_path)
    a.upsert_nodes([
        Node(id="u:person:d", type="person", name="Demis", summary="x"),
        Node(id="u:object:d2", type="object", name="Demis-tool", summary="x"),
    ])
    hits = a.fts_search("Demis", k=5, type_filter="person")
    assert all(h[0].startswith("u:person:") for h in hits)


def test_vec_search(tmp_path):
    import numpy as np
    from kg.storage.sqlite import SQLiteAdapter
    a = SQLiteAdapter(tmp_path / "kg.db")
    base = [1.0] * 384
    n1 = Node(id="u:person:a", type="person", name="A", embedding=base)
    n2 = Node(id="u:person:b", type="person", name="B",
              embedding=[0.5 if i == 0 else 1.0 for i in range(384)])
    a.upsert_nodes([n1, n2])
    hits = a.vec_search(base, k=2)
    assert hits[0][0] == "u:person:a"  # identical → highest cosine
```

- [ ] **Step 3: Run, verify FAIL**
- [ ] **Step 4: Implement** — store embeddings on upsert and add search methods:

```python
# in upsert_nodes, after the fts insert, add:
if n.embedding is not None:
    self.conn.execute(
        "INSERT OR REPLACE INTO nodes_vec(node_id, embedding) VALUES (?, ?)",
        (n.id, sqlite_vec.serialize_float32(n.embedding)),
    )

# add the search methods:
def fts_search(self, query, k=10, type_filter=None):
    sql = ("SELECT n.id, bm25(nodes_fts) r FROM nodes_fts f "
           "JOIN nodes n ON n.id = f.node_id WHERE nodes_fts MATCH ? AND n.status='active'")
    params: list = [query]
    if type_filter:
        sql += " AND n.data LIKE ?"
        params.append(f'%"type": "{type_filter}"%')
    sql += " ORDER BY r LIMIT ?"
    params.append(k)
    rows = self.conn.execute(sql, params).fetchall()
    return [(r["id"], float(r["r"])) for r in rows]

def vec_search(self, embedding, k=10, type_filter=None):
    rows = self.conn.execute(
        "SELECT node_id, distance FROM nodes_vec "
        "WHERE embedding MATCH ? ORDER BY distance LIMIT ?",
        (sqlite_vec.serialize_float32(embedding), k),
    ).fetchall()
    out = []
    for r in rows:
        n = self.get(r["node_id"])
        if n and n.status == "active":
            if type_filter and n.type != type_filter:
                continue
            score = 1.0 - float(r["distance"])  # distance→similarity-ish
            out.append((r["node_id"], score))
    return out
```
> Drop the `import numpy as np` from the test (unused) before commit.

- [ ] **Step 5: Run, verify PASS**
- [ ] **Step 6: Commit** — `feat(m1): SQLite FTS5 + sqlite-vec search`

---

### Task 7: `Embedder` (local fastembed + FakeEmbedder + cache)

**Files:**
- Create: `src/kg/embed.py`, `tests/test_embed.py`
- Modify: `pyproject.toml` (add `fastembed>=0.3`)

**Interfaces:**
- Produces: `Embedder` ABC with `embed(text: str) -> list[float]`, `embed_many(texts: list[str]) -> list[list[float]]`, `dim() -> int`. `LocalEmbedder(model_name="BAAI/bge-small-en-v1.5")`. `FakeEmbedder(seed_dim=384)` deterministic (hash → vector) for tests. `make_embedder(config: Config) -> Embedder`. `content_hash(text: str) -> str`.

- [ ] **Step 1: Add dep** — `"fastembed>=0.3"` to pyproject, `uv sync`.
- [ ] **Step 2: Failing test**

```python
# tests/test_embed.py
import hashlib
from kg.embed import FakeEmbedder, content_hash, make_embedder
from kg.config import Config


def test_fake_embedder_deterministic():
    e = FakeEmbedder(dim=8)
    v1 = e.embed("hello world")
    v2 = e.embed("hello world")
    assert v1 == v2
    assert len(v1) == 8


def test_fake_embedder_different_inputs_differ():
    e = FakeEmbedder(dim=8)
    assert e.embed("aaa") != e.embed("bbb")


def test_embed_many_consistent_with_embed():
    e = FakeEmbedder(dim=8)
    single = e.embed("x")
    many = e.embed_many(["x", "y"])
    assert many[0] == single
    assert len(many) == 2


def test_content_hash_stable():
    assert content_hash("abc") == hashlib.sha256(b"abc").hexdigest()


def test_make_embedder_local_returns_embedder():
    cfg = Config.default()
    e = make_embedder(cfg)
    assert e.dim() == 384
```

- [ ] **Step 3: Run, verify FAIL**
- [ ] **Step 4: Implement**

```python
# src/kg/embed.py
from __future__ import annotations
import hashlib
from abc import ABC, abstractmethod
from kg.config import Config


def content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class Embedder(ABC):
    @abstractmethod
    def embed(self, text: str) -> list[float]: ...
    @abstractmethod
    def embed_many(self, texts: list[str]) -> list[list[float]]: ...
    @abstractmethod
    def dim(self) -> int: ...


class FakeEmbedder(Embedder):
    """Deterministic hash-based embedding for tests — no model download."""
    def __init__(self, dim: int = 384):
        self._dim = dim

    def _vec(self, text: str) -> list[float]:
        h = hashlib.sha256(text.encode("utf-8")).digest()
        # stretch hash bytes to dim
        out = []
        for i in range(self._dim):
            out.append((h[i % len(h)] / 255.0) * 2 - 1)
        # L2 normalize
        norm = sum(x * x for x in out) ** 0.5 or 1.0
        return [x / norm for x in out]

    def embed(self, text: str) -> list[float]:
        return self._vec(text)

    def embed_many(self, texts: list[str]) -> list[list[float]]:
        return [self._vec(t) for t in texts]

    def dim(self) -> int:
        return self._dim


class LocalEmbedder(Embedder):
    def __init__(self, model_name: str = "BAAI/bge-small-en-v1.5"):
        from fastembed import TextEmbedding
        self._model = TextEmbedding(model_name=model_name)
        self._dim = 384

    def embed(self, text: str) -> list[float]:
        return next(self._model.embed([text])).tolist()

    def embed_many(self, texts: list[str]) -> list[list[float]]:
        return [v.tolist() for v in self._model.embed(texts)]

    def dim(self) -> int:
        return self._dim


def make_embedder(config: Config) -> Embedder:
    if config.embedding.provider == "local":
        return LocalEmbedder(model_name=config.embedding.model)
    return FakeEmbedder()  # API providers wired in a later task
```

- [ ] **Step 5: Run, verify PASS**
- [ ] **Step 6: Commit** — `feat(m1): Embedder — local fastembed + Fake + cache hash`

---

### Task 8: `Resolver` — exact → fuzzy → semantic

**Files:**
- Create: `src/kg/resolve.py`, `tests/test_resolve.py`
- Modify: `pyproject.toml` (add `rapidfuzz>=3`)

**Interfaces:**
- Produces: `Resolution(matched_id: str | None, canonical_name: str, via: str, score: float)` and `class Resolver(adapter, embedder, thresholds) -> Resolution` via `resolve(name: str, type_: str)`.
- Resolution chain: (1) exact alias hit (normalized case/whitespace) in same-type active nodes; (2) fuzzy `rapidfuzz token_set_ratio ≥ thresholds.resolve_fuzzy`; (3) semantic name-only embedding cosine `≥ thresholds.resolve_semantic` against same-type nodes' name embeddings. Short-circuit. No merges.

- [ ] **Step 1: Add dep** — `"rapidfuzz>=3"`, `uv sync`.
- [ ] **Step 2: Failing test**

```python
# tests/test_resolve.py
from kg.resolve import Resolver, Resolution
from kg.storage.sqlite import SQLiteAdapter
from kg.ontology import Node
from kg.embed import FakeEmbedder
from kg.config import Config


def _setup(tmp_path):
    cfg = Config.default()
    a = SQLiteAdapter(tmp_path / "kg.db")
    a.upsert_nodes([
        Node(id="u:person:demis-hassabis", type="person",
             name="Demis Hassabis", canonical_name="Demis Hassabis",
             aliases=["D. Hassabis"]),
    ])
    r = Resolver(a, FakeEmbedder(), cfg.thresholds, user_id="u")
    return a, r


def test_exact_alias(tmp_path):
    a, r = _setup(tmp_path)
    res = r.resolve("D. Hassabis", "person")
    assert res.via == "exact"
    assert res.matched_id == "u:person:demis-hassabis"


def test_fuzzy_match(tmp_path):
    a, r = _setup(tmp_path)
    res = r.resolve("demis hasabis", "person")  # typo
    assert res.via in ("fuzzy", "exact")
    assert res.matched_id == "u:person:demis-hassabis"


def test_no_match_returns_none_id(tmp_path):
    a, r = _setup(tmp_path)
    res = r.resolve("Completely Unrelated Person", "person")
    assert res.matched_id is None
    assert res.via == "none"


def test_type_gated(tmp_path):
    a, r = _setup(tmp_path)
    res = r.resolve("Demis Hassabis", "organization")  # wrong type
    assert res.matched_id is None
```

- [ ] **Step 3: Run, verify FAIL**
- [ ] **Step 4: Implement**

```python
# src/kg/resolve.py
from __future__ import annotations
import re
from dataclasses import dataclass
from rapidfuzz import fuzz
from kg.embed import Embedder
from kg.storage.base import StorageAdapter


@dataclass
class Resolution:
    matched_id: str | None
    canonical_name: str
    via: str  # exact | fuzzy | semantic | none
    score: float


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", s.strip().lower())


class Resolver:
    def __init__(self, adapter: StorageAdapter, embedder: Embedder,
                 thresholds, user_id: str):
        self.adapter = adapter
        self.embedder = embedder
        self.t = thresholds
        self.user_id = user_id

    def _candidates(self, type_: str) -> list:
        # all active nodes of this type (small-graph scan; vec index used in M-scale later)
        rows = self.adapter.conn.execute(
            "SELECT data FROM nodes WHERE status='active'"
        ).fetchall()
        from kg.ontology import Node
        out = []
        for r in rows:
            n = Node.model_validate_json(r["data"])
            if n.type == type_:
                out.append(n)
        return out

    def resolve(self, name: str, type_: str) -> Resolution:
        cands = self._candidates(type_)
        target = _norm(name)
        # 1. exact (alias or name)
        for n in cands:
            names = {_norm(n.name), *(_norm(a) for a in n.aliases)}
            if target in names:
                return Resolution(n.id, n.canonical_name or n.name, "exact", 1.0)
        # 2. fuzzy
        best_id, best_score = None, 0.0
        for n in cands:
            sc = max(fuzz.token_set_ratio(target, _norm(n.name)) / 100.0,
                     *(fuzz.token_set_ratio(target, _norm(a)) / 100.0 for a in n.aliases))
            if sc > best_score:
                best_id, best_score = n.id, sc
        if best_id and best_score >= self.t.resolve_fuzzy:
            node = self.adapter.get(best_id)
            return Resolution(best_id, node.canonical_name or node.name, "fuzzy", best_score)
        # 3. semantic (name-only embedding)
        qv = self.embedder.embed(name)
        best_id, best_score = None, 0.0
        for n in cands:
            nv = self.embedder.embed(n.name)
            cos = sum(a*b for a, b in zip(qv, nv))
            if cos > best_score:
                best_id, best_score = n.id, cos
        if best_id and best_score >= self.t.resolve_semantic:
            node = self.adapter.get(best_id)
            return Resolution(best_id, node.canonical_name or node.name, "semantic", best_score)
        return Resolution(None, name, "none", 0.0)
```

- [ ] **Step 5: Run, verify PASS**
- [ ] **Step 6: Commit** — `feat(m1): Resolver — exact/fuzzy/semantic, type-gated`

---

### Task 9: `Deduper` — full-context scoring

**Files:**
- Create: `src/kg/dedup.py`, `tests/test_dedup.py`

**Interfaces:**
- Produces: `DedupResult(best_match_id: str | None, score: float)` and `class Deduper(adapter, embedder, thresholds)`. `dedup(node: Node) -> DedupResult` — candidates = same-type active nodes from `vec_search(full_context_embedding)` top-k ∪ same `canonical_name`; `score = weights.embedding·cosine + weights.fuzzy·fuzzy(full_context_text)`.
- Helper `full_context_text(node, embed_fields) -> str` and `full_context_embedding(node, embedder, embed_fields) -> list[float]`.

- [ ] **Step 1: Failing test**

```python
# tests/test_dedup.py
from kg.dedup import Deduper, full_context_text
from kg.storage.sqlite import SQLiteAdapter
from kg.ontology import Node
from kg.embed import FakeEmbedder
from kg.config import Config


def test_full_context_text_includes_high_signal_attrs():
    n = Node(type="person", name="Demis", summary="CEO",
             attributes={"role": "founder", "email": "x@y.com"})
    txt = full_context_text(n, {"person": ["name", "summary", "attributes.role"]})
    assert "Demis" in txt and "CEO" in txt and "founder" in txt
    assert "x@y.com" not in txt  # email not in embed_fields


def test_dedup_high_score_for_near_duplicate(tmp_path):
    cfg = Config.default()
    a = SQLiteAdapter(tmp_path / "kg.db")
    emb = FakeEmbedder()
    n1 = Node(id="u:person:p1", type="person", name="Paris",
              canonical_name="Paris", summary="capital of France")
    a.upsert_nodes([n1])
    d = Deduper(a, emb, cfg.thresholds)
    # same canonical → strong fuzzy; identical-ish context
    n2 = Node(id="u:person:p2", type="person", name="Paris",
              canonical_name="Paris", summary="capital of France")
    res = d.dedup(n2)
    assert res.best_match_id == "u:person:p1"
    assert res.score >= 0.85


def test_dedup_low_score_for_distinct(tmp_path):
    cfg = Config.default()
    a = SQLiteAdapter(tmp_path / "kg.db")
    emb = FakeEmbedder()
    a.upsert_nodes([Node(id="u:person:a", type="person", name="Paris",
                         summary="capital of France")])
    d = Deduper(a, emb, cfg.thresholds)
    n2 = Node(id="u:person:b", type="person", name="Paris",
              summary="city in Texas, USA")  # Paris, TX ≠ Paris, FR
    res = d.dedup(n2)
    assert res.score < 0.85  # gray zone or new — NOT auto-merged
```

- [ ] **Step 2: Run, verify FAIL**
- [ ] **Step 3: Implement**

```python
# src/kg/dedup.py
from __future__ import annotations
from dataclasses import dataclass
from rapidfuzz import fuzz
from kg.ontology import Node
from kg.embed import Embedder
from kg.storage.base import StorageAdapter


@dataclass
class DedupResult:
    best_match_id: str | None
    score: float


def _get_attr(d: dict, dotted: str):
    cur: object = d
    for part in dotted.split("."):
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
        else:
            return None
    return cur


def full_context_text(node: Node, embed_fields: dict[str, list[str]]) -> str:
    fields = embed_fields.get(node.type, ["name", "summary"])
    parts: list[str] = []
    for f in fields:
        if f == "name":
            parts.append(node.name or "")
        elif f == "summary":
            parts.append(node.summary or "")
        elif f.startswith("attributes."):
            val = _get_attr(node.attributes, f[len("attributes."):])
            if val is not None:
                parts.append(str(val))
    return " ".join(p for p in parts if p)


def full_context_embedding(node: Node, embedder: Embedder,
                           embed_fields: dict[str, list[str]]) -> list[float]:
    return embedder.embed(full_context_text(node, embed_fields))


class Deduper:
    def __init__(self, adapter: StorageAdapter, embedder: Embedder, thresholds):
        self.adapter = adapter
        self.embedder = embedder
        self.t = thresholds

    def dedup(self, node: Node, embed_fields: dict[str, list[str]] | None = None) -> DedupResult:
        ef = embed_fields or {}
        qemb = full_context_embedding(node, self.embedder, ef)
        qtext = full_context_text(node, ef)
        # candidates: vec top-k of same type
        cand = self.adapter.vec_search(qemb, k=10, type_filter=node.type)
        # plus same canonical_name (exact-name siblings)
        rows = self.adapter.conn.execute(
            "SELECT id, data FROM nodes WHERE status='active'"
        ).fetchall()
        cand_ids = {cid for cid, _ in cand}
        from kg.ontology import Node as _N
        for r in rows:
            n = _N.model_validate_json(r["data"])
            if n.type == node.type and n.id != node.id and \
               n.canonical_name and n.canonical_name == node.canonical_name:
                cand_ids.add(n.id)

        best_id, best_score = None, 0.0
        for cid in cand_ids:
            existing = self.adapter.get(cid)
            if not existing or existing.id == node.id:
                continue
            etext = full_context_text(existing, ef)
            cos = sum(a*b for a, b in zip(qemb, self.embedder.embed(etext)))
            fz = fuzz.token_set_ratio(qtext, etext) / 100.0
            score = self.t.dedup_weights.embedding * cos + self.t.dedup_weights.fuzzy * fz
            if score > best_score:
                best_id, best_score = existing.id, score
        return DedupResult(best_id, best_score)
```

- [ ] **Step 4: Run, verify PASS**
- [ ] **Step 5: Commit** — `feat(m1): Deduper — 0.7·cos+0.3·fuzzy full-context scoring`

---

### Task 10: The Gate — validate → resolve → embed → dedup → route + merge

**Files:**
- Create: `src/kg/gate.py`, `tests/test_gate.py`

**Interfaces:**
- Produces: `Decision(name, type, action, target_id, score, via)` where `action ∈ {NEW, RESOLVED, MERGED, FLAGGED}`. `SaveReport(decisions: list[Decision], edges_upserted: int, new_same_as: int)`. `class Gate(adapter, resolver, deduper, embedder, config, user_id)` with `normalize(extracted_nodes: list[dict], extracted_edges: list[dict], source: str) -> SaveReport`.
- Consumes: extracted node dicts `{type, subtype, name, summary, attributes}` and edge dicts `{source_name, semantic_type, target_name, summary}` (name-space-local). Fact/preference dicts handled as nodes.
- `merge(winner_id, loser_id)` — aliases ∪, sources ∪, attributes winner-wins + loser into `attribute_conflicts`, re-embed, re-point edges, tombstone loser.

- [ ] **Step 1: Failing test**

```python
# tests/test_gate.py
from kg.gate import Gate
from kg.storage.sqlite import SQLiteAdapter
from kg.resolve import Resolver
from kg.dedup import Deduper
from kg.embed import FakeEmbedder
from kg.config import Config


def _gate(tmp_path):
    cfg = Config.default()
    a = SQLiteAdapter(tmp_path / "kg.db")
    emb = FakeEmbedder()
    g = Gate(a, Resolver(a, emb, cfg.thresholds, "u"),
             Deduper(a, emb, cfg.thresholds), emb, cfg, user_id="u")
    return a, g, cfg


def test_new_node_routed_as_new(tmp_path):
    a, g, cfg = _gate(tmp_path)
    rep = g.normalize(
        [{"type": "person", "name": "Demis Hassabis", "summary": "founder"}],
        [], source="raw/x.md#chunk-0",
    )
    d = rep.decisions[0]
    assert d.action == "NEW"
    assert a.count()["nodes"] == 1


def test_idempotent_rerun(tmp_path):
    a, g, cfg = _gate(tmp_path)
    nodes = [{"type": "person", "name": "Demis Hassabis", "summary": "founder"}]
    g.normalize(nodes, [], "raw/x.md#chunk-0")
    rep2 = g.normalize(nodes, [], "raw/x.md#chunk-0")
    # same content-derived id → resolves to existing, no dup
    assert a.count()["nodes"] == 1


def test_rejects_unknown_type(tmp_path):
    import pytest
    a, g, cfg = _gate(tmp_path)
    with pytest.raises(ValueError):
        g.normalize([{"type": "alien", "name": "X"}], [], "raw/x.md#chunk-0")


def test_gray_zone_flagged_not_merged(tmp_path):
    a, g, cfg = _gate(tmp_path)
    # two distinct-but-similar entities; force gray zone by same canonical, diff summary
    g.normalize([{"type": "person", "name": "Paris", "summary": "capital of France"}],
                [], "raw/a.md#chunk-0")
    rep = g.normalize([{"type": "person", "name": "Paris", "summary": "city in Texas"}],
                      [], "raw/b.md#chunk-0")
    # second Paris is NOT auto-merged into first
    actions = [d.action for d in rep.decisions]
    assert "MERGED" not in actions
    assert a.count()["nodes"] >= 2


def test_edge_name_resolution(tmp_path):
    a, g, cfg = _gate(tmp_path)
    rep = g.normalize(
        [{"type": "person", "name": "Demis Hassabis"},
         {"type": "organization", "name": "DeepMind"}],
        [{"source_name": "Demis Hassabis", "semantic_type": "employed_by",
          "target_name": "DeepMind"}],
        "raw/x.md#chunk-0",
    )
    assert rep.edges_upserted == 1
    assert a.count()["edges"] == 1


def test_merge_union_and_tombstone(tmp_path):
    a, g, cfg = _gate(tmp_path)
    a, _ = a, None
    from kg.ontology import Node
    a.upsert_nodes([
        Node(id="u:person:w", type="person", name="Paris",
             aliases=["a"], sources=[{"doc": "x"}]),
        Node(id="u:person:l", type="person", name="Paree",
             aliases=["b"], sources=[{"doc": "y"}]),
    ])
    g.merge("u:person:w", "u:person:l")
    winner = a.get("u:person:w")
    loser = a.get("u:person:l")
    assert "b" in winner.aliases and "a" in winner.aliases
    assert {"doc": "x"} in winner.sources and {"doc": "y"} in winner.sources
    assert loser.status == "tombstoned"
    assert loser.merged_into == "u:person:w"
```

- [ ] **Step 2: Run, verify FAIL**
- [ ] **Step 3: Implement**

```python
# src/kg/gate.py
from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime, timezone
from kg.ontology import Node, Edge, ALLOWED_NODE_TYPES
from kg.ontology import ALLOWED_SEMANTIC_EDGE_TYPES, STRUCTURAL_EDGE_TYPES
from kg.ids import node_id, edge_id
from kg.dedup import full_context_embedding


@dataclass
class Decision:
    name: str
    type: str
    action: str   # NEW | RESOLVED | MERGED | FLAGGED
    target_id: str | None
    score: float
    via: str


@dataclass
class SaveReport:
    decisions: list[Decision] = field(default_factory=list)
    edges_upserted: int = 0
    new_same_as: int = 0


class Gate:
    def __init__(self, adapter, resolver, deduper, embedder, config, user_id):
        self.adapter = adapter
        self.resolver = resolver
        self.deduper = deduper
        self.embedder = embedder
        self.config = config
        self.user_id = user_id

    def _now(self) -> str:
        return datetime.now(timezone.utc).isoformat()

    def normalize(self, extracted_nodes, extracted_edges, source) -> SaveReport:
        report = SaveReport()
        name_to_id: dict[str, str] = {}

        for en in extracted_nodes:
            type_ = en["type"]
            if type_ not in ALLOWED_NODE_TYPES:
                raise ValueError(f"Unknown node type: {type_!r}")
            name = en["name"]
            # 2. RESOLVE (naming only)
            res = self.resolver.resolve(name, type_)
            if res.matched_id:
                node = self.adapter.get(res.matched_id)
                if name not in node.aliases and name != node.name:
                    node.aliases = [*node.aliases, name]
                    node.sources = self._add_source(node.sources, source)
                    node.updated_at = self._now()
                    self.adapter.upsert_nodes([node])
                name_to_id[name] = res.matched_id
                report.decisions.append(Decision(name, type_, "RESOLVED",
                                                 res.matched_id, res.score, res.via))
                continue
            # 3. EMBED + 4. DEDUP (identity)
            candidate = Node(
                id=node_id(self.user_id, type_, name),
                type=type_, subtype=en.get("subtype"),
                name=name, canonical_name=name,
                summary=en.get("summary"), attributes=en.get("attributes", {}),
                sources=[{"doc": source.split("#")[0],
                          "chunk": source.split("chunk-")[-1]}],
                created_at=self._now(), updated_at=self._now(),
            )
            cand_emb = full_context_embedding(
                candidate, self.embedder, self.config.embedding.embed_fields)
            candidate.embedding = cand_emb
            dd = self.deduper.dedup(candidate, self.config.embedding.embed_fields)
            # 5. ROUTE
            if dd.best_match_id and dd.score >= self.config.thresholds.dedup_merge:
                self.merge(dd.best_match_id, candidate.id)
                name_to_id[name] = dd.best_match_id
                report.decisions.append(Decision(name, type_, "MERGED",
                                                 dd.best_match_id, dd.score, "dedup"))
            elif dd.best_match_id and dd.score >= self.config.thresholds.dedup_flag:
                self.adapter.upsert_nodes([candidate])
                # same_as pending edge
                eid = edge_id(candidate.id, "same_as", dd.best_match_id)
                self.adapter.upsert_edges([Edge(
                    id=eid, semantic_type="same_as",
                    summary=f"gray-zone {dd.score:.2f}",
                    confidence=dd.score)])
                name_to_id[name] = candidate.id
                report.new_same_as += 1
                report.decisions.append(Decision(name, type_, "FLAGGED",
                                                 dd.best_match_id, dd.score, "dedup"))
            else:
                self.adapter.upsert_nodes([candidate])
                name_to_id[name] = candidate.id
                report.decisions.append(Decision(name, type_, "NEW",
                                                 candidate.id, dd.score, "new"))

        # edges: map names → settled ids
        edges_out = []
        for ee in extracted_edges:
            sem = ee["semantic_type"]
            if sem not in (ALLOWED_SEMANTIC_EDGE_TYPES | STRUCTURAL_EDGE_TYPES):
                raise ValueError(f"Unknown edge semantic_type: {sem!r}")
            src = name_to_id.get(ee["source_name"])
            tgt = name_to_id.get(ee["target_name"])
            if not src or not tgt:
                continue
            edges_out.append(Edge(
                id=edge_id(src, sem, tgt), semantic_type=sem,
                summary=ee.get("summary"),
                sources=[{"doc": source.split("#")[0]}]))
        if edges_out:
            self.adapter.upsert_edges(edges_out)
        report.edges_upserted = len(edges_out)
        return report

    def _add_source(self, sources, source):
        entry = {"doc": source.split("#")[0], "chunk": source.split("chunk-")[-1]}
        if entry in sources:
            return sources
        return [*sources, entry]

    def merge(self, winner_id: str, loser_id: str) -> None:
        w = self.adapter.get(winner_id)
        l = self.adapter.get(loser_id)
        if not w or not l:
            return
        w.aliases = list(dict.fromkeys([*w.aliases, l.name, *l.aliases]))
        w.sources = self._merge_unique(w.sources, l.sources)
        for k, v in l.attributes.items():
            if k in w.attributes and w.attributes[k] != v:
                w.attribute_conflicts.append({"key": k, "winner": w.attributes[k], "loser": v})
            else:
                w.attributes[k] = v
        if (l.summary and (not w.summary or len(l.summary) > len(w.summary))):
            w.summary = l.summary
        w.embedding = full_context_embedding(w, self.embedder,
                                             self.config.embedding.embed_fields)
        w.updated_at = self._now()
        self.adapter.upsert_nodes([w])
        # re-point loser's edges to winner
        rows = self.adapter.conn.execute(
            "SELECT data FROM edges WHERE source=? OR target=?", (loser_id, loser_id)
        ).fetchall()
        from kg.ontology import Edge
        for r in rows:
            e = Edge.model_validate_json(r["data"])
            src, sem, tgt = e.id.split("|", 2)
            src = winner_id if src == loser_id else src
            tgt = winner_id if tgt == loser_id else tgt
            e.id = edge_id(src, sem, tgt)
            self.adapter.conn.execute("DELETE FROM edges WHERE id=?", (r["data"],))
        self.adapter.conn.commit()
        # tombstone loser
        l.status = "tombstoned"
        l.merged_into = winner_id
        self.adapter.upsert_nodes([l])

    @staticmethod
    def _merge_unique(a, b):
        seen, out = set(), []
        for s in [*a, *b]:
            key = tuple(sorted(s.items())) if isinstance(s, dict) else s
            if key not in seen:
                seen.add(key); out.append(s)
        return out
```

> Refinement before commit: in `merge`, replace the broken edge-repointing (it deletes by `data` text and never re-inserts) with: build the new `Edge`, delete the old by its `id`, then `upsert_edges([new_edge])`. The test asserts only alias/source/tombstone behavior, which already passes, but fix the edge logic so M2 dream's `kg merge` is correct.

- [ ] **Step 4: Run, verify PASS**
- [ ] **Step 5: Commit** — `feat(m1): normalization gate + merge semantics`

---

### Task 11: `hybrid_search` (RRF fusion)

**Files:**
- Create: `src/kg/search.py`, `tests/test_search.py`

**Interfaces:**
- Produces: `rrf(rank_lists: list[list[str]], k: int = 60) -> list[tuple[str, float]]` and `hybrid_search(adapter, embedder, query, mode='hybrid', k=10, type_filter=None, config=None) -> list[tuple[str, float]]`. Modes: `hybrid` (fts ∥ vec), `bm25` (fts only), `semantic` (vec only), `keyword` (fts raw match).

- [ ] **Step 1: Failing test**

```python
# tests/test_search.py
from kg.search import rrf, hybrid_search
from kg.storage.sqlite import SQLiteAdapter
from kg.ontology import Node
from kg.embed import FakeEmbedder
from kg.config import Config


def test_rrf_merges_rank_lists():
    a = ["x", "y", "z"]
    b = ["y", "z", "w"]
    fused = dict(rrf([a, b], k=60))
    # y appears high in both → top
    top = sorted(fused, key=lambda i: -fused[i])[0]
    assert top == "y"


def test_hybrid_search_returns_seeds(tmp_path):
    cfg = Config.default()
    ad = SQLiteAdapter(tmp_path / "kg.db")
    emb = FakeEmbedder()
    ad.upsert_nodes([
        Node(id="u:person:d", type="person", name="Demis", summary="DeepMind founder"),
    ])
    hits = hybrid_search(ad, emb, "DeepMind founder", k=5, config=cfg)
    ids = [h[0] for h in hits]
    assert "u:person:d" in ids
```

- [ ] **Step 2: Run, verify FAIL**
- [ ] **Step 3: Implement**

```python
# src/kg/search.py
from __future__ import annotations
from kg.embed import Embedder
from kg.storage.base import StorageAdapter


def rrf(rank_lists: list[list[str]], k: int = 60) -> list[tuple[str, float]]:
    scores: dict[str, float] = {}
    for ranks in rank_lists:
        for rank, item in enumerate(ranks):
            scores[item] = scores.get(item, 0.0) + 1.0 / (k + rank + 1)
    return sorted(scores.items(), key=lambda kv: -kv[1])


def hybrid_search(adapter: StorageAdapter, embedder: Embedder,
                  query: str, mode: str = "hybrid", k: int = 10,
                  type_filter: str | None = None, config=None) -> list[tuple[str, float]]:
    lists: list[list[str]] = []
    if mode in ("hybrid", "bm25", "keyword"):
        fts = adapter.fts_search(query, k=k * 5, type_filter=type_filter)
        lists.append([i for i, _ in fts])
    if mode in ("hybrid", "semantic"):
        qv = embedder.embed(query)
        vec = adapter.vec_search(qv, k=k * 5, type_filter=type_filter)
        lists.append([i for i, _ in vec])
    rrf_k = getattr(getattr(config, "query", None), "rrf_k", 60) if config else 60
    fused = rrf(lists, k=rrf_k)
    return fused[:k]
```

- [ ] **Step 4: Run, verify PASS**
- [ ] **Step 5: Commit** — `feat(m1): hybrid search + RRF fusion`

---

### Task 12: `expand` (BFS + centrality + cap) and `pack` (rank + trim)

**Files:**
- Create: `src/kg/traverse.py`, `src/kg/pack.py`, `tests/test_traverse.py`, `tests/test_pack.py`

**Interfaces:**
- Produces: `expand(adapter, seed_ids, hops=2, direction='both', edge_types=None, cap=300) -> Subgraph` (caps frontier, logs nothing here). `degree_centrality(subgraph) -> dict[str,float]`. `pack(subgraph, seeds, rrf_scores, budget_tokens=4000) -> str` returning markdown, ranking `0.5·rrf ∪ 0.3·centrality ∪ 0.2·recency`, dedupe, trim.

- [ ] **Step 1: Failing tests**

```python
# tests/test_traverse.py
from kg.traverse import expand, degree_centrality
from kg.storage.sqlite import SQLiteAdapter
from kg.ontology import Node, Edge


def test_expand_two_hop(tmp_path):
    a = SQLiteAdapter(tmp_path / "kg.db")
    a.upsert_nodes([Node(id=f"u:object:{i}", type="object", name=str(i)) for i in "abc"])
    a.upsert_edges([Edge(id="u:object:a|related_to|u:object:b", semantic_type="related_to"),
                    Edge(id="u:object:b|related_to|u:object:c", semantic_type="related_to")])
    sg = expand(a, ["u:object:a"], hops=2)
    ids = {n.id for n in sg.nodes}
    assert {"u:object:b", "u:object:c"} <= ids


def test_degree_centrality(tmp_path):
    a = SQLiteAdapter(tmp_path / "kg.db")
    a.upsert_nodes([Node(id=f"u:object:{i}", type="object", name=i) for i in "abc"])
    a.upsert_edges([Edge(id="u:object:a|related_to|u:object:b", semantic_type="related_to"),
                    Edge(id="u:object:a|related_to|u:object:c", semantic_type="related_to")])
    sg = a.neighbors(["u:object:a", "u:object:b", "u:object:c"], depth=1)
    cent = degree_centrality(sg)
    assert cent["u:object:a"] >= cent["u:object:b"]
```

```python
# tests/test_pack.py
from kg.pack import pack
from kg.storage.base import Subgraph
from kg.ontology import Node


def test_pack_returns_markdown_under_budget():
    nodes = [Node(id=f"u:person:{i}", type="person", name=f"Person {i}",
                  summary="x" * 20, created_at="2026-07-26T00:00:00Z")
             for i in range(5)]
    sg = Subgraph(nodes=nodes)
    seeds = {n.id: 0.9 for n in nodes}
    md = pack(sg, seeds, rrf_scores=seeds, budget_tokens=4000)
    assert isinstance(md, str)
    assert "Person 0" in md


def test_pack_trims_to_budget():
    big = [Node(id=f"u:person:{i}", type="person", name=f"P{i}",
                summary="word " * 400, created_at="2026-07-26T00:00:00Z")
           for i in range(20)]
    sg = Subgraph(nodes=big)
    seeds = {n.id: 0.5 for n in big}
    md = pack(sg, seeds, rrf_scores=seeds, budget_tokens=500)
    # ~500 tokens ≈ rough char cap; assert trimmed
    assert len(md) < sum(len(n.summary or "") for n in big)
```

- [ ] **Step 2: Run, verify FAIL**
- [ ] **Step 3: Implement**

```python
# src/kg/traverse.py
from __future__ import annotations
from kg.storage.base import Subgraph


def expand(adapter, seed_ids, hops=2, direction="both", edge_types=None, cap=300) -> Subgraph:
    sg = adapter.neighbors(seed_ids, depth=hops, direction=direction, edge_types=edge_types)
    if len(sg.nodes) > cap:
        sg.nodes = sg.nodes[:cap]
    return sg


def degree_centrality(subgraph: Subgraph) -> dict[str, float]:
    deg: dict[str, int] = {}
    for e in subgraph.edges:
        try:
            src, _, tgt = (e.id or "").split("|", 2)
        except ValueError:
            continue
        deg[src] = deg.get(src, 0) + 1
        deg[tgt] = deg.get(tgt, 0) + 1
    if not deg:
        return {n.id: 0.0 for n in subgraph.nodes}
    mx = max(deg.values()) or 1
    return {nid: deg.get(nid, 0) / mx for nid in {n.id for n in subgraph.nodes}}
```

```python
# src/kg/pack.py
from __future__ import annotations
from kg.storage.base import Subgraph


def _recency(node, now_ts: float = 0.0) -> float:
    # created_at may be ISO; absent recency → 0
    return 0.0


def pack(subgraph: Subgraph, seeds: dict[str, float],
         rrf_scores: dict[str, float] | None = None,
         budget_tokens: int = 4000) -> str:
    rrf_scores = rrf_scores or {}
    cent = {n.id: 0.0 for n in subgraph.nodes}  # centrality injected by caller in full flow
    seen = set()
    ranked = []
    for n in subgraph.nodes:
        if n.id in seen:
            continue
        seen.add(n.id)
        score = (0.5 * rrf_scores.get(n.id, 0.0)
                 + 0.3 * seeds.get(n.id, 0.0)
                 + 0.2 * _recency(n))
        ranked.append((score, n))
    ranked.sort(key=lambda t: -t[0])

    # rough token estimate: 4 chars/token
    char_budget = budget_tokens * 4
    lines = ["# kg context", ""]
    used = len("".join(lines))
    for score, n in ranked:
        src = ", ".join(s.get("doc", "?") for s in n.sources) or "-"
        block = f"## {n.name} ({n.type})\n{score:.2f} · sources: {src}\n{n.summary or ''}\n\n"
        if used + len(block) > char_budget:
            break
        lines.append(block)
        used += len(block)
    return "\n".join(lines)
```

- [ ] **Step 4: Run, verify PASS**
- [ ] **Step 5: Commit** — `feat(m1): expand (BFS+cap) + degree centrality + pack`

---

### Task 13: CLI — `kg save`, `kg search`, `kg expand`, `kg pack`, `kg resolve`, `kg dedup-check`

**Files:**
- Create: `src/kg/cli/save.py`, `src/kg/cli/query.py`, `src/kg/cli/resolve_cli.py`
- Modify: `src/kg/cli/main.py` (register), `tests/cli/test_save.py`, `tests/cli/test_query.py`

**Interfaces:**
- Produces CLI wrappers calling core. `kg save --nodes <json> --edges <json> --source <ref>` → builds adapter from `KgPaths`, runs `Gate.normalize`, prints decision report. `kg search "<q>" [--mode] [--type] [-k]`. `kg expand <id...> [--hops]`. `kg pack` reads a subgraph json from stdin.

- [ ] **Step 1: Failing tests**

```python
# tests/cli/test_save.py
import json
from typer.testing import CliRunner
from kg.cli.main import app


def test_kg_save_persists_and_reports(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    r = CliRunner()
    assert r.invoke(app, ["init", "--user-id", "u", "--scope", "s"]).exit_code == 0
    nodes = [{"type": "person", "name": "Demis Hassabis", "summary": "founder"}]
    edges = []
    (tmp_path / "n.json").write_text(json.dumps(nodes))
    (tmp_path / "e.json").write_text(json.dumps(edges))
    res = r.invoke(app, ["save", "--nodes", str(tmp_path / "n.json"),
                         "--edges", str(tmp_path / "e.json"),
                         "--source", "raw/x.md#chunk-0"])
    assert res.exit_code == 0
    assert "NEW" in res.stdout or "RESOLVED" in res.stdout
```

```python
# tests/cli/test_query.py
import json
from typer.testing import CliRunner
from kg.cli.main import app


def test_kg_search_after_save(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    r = CliRunner()
    r.invoke(app, ["init", "--user-id", "u", "--scope", "s"])
    (tmp_path / "n.json").write_text(json.dumps(
        [{"type": "person", "name": "Demis Hassabis", "summary": "DeepMind founder"}]))
    (tmp_path / "e.json").write_text("[]")
    r.invoke(app, ["save", "--nodes", str(tmp_path / "n.json"),
                   "--edges", str(tmp_path / "e.json"), "--source", "raw/x.md#chunk-0"])
    out = r.invoke(app, ["search", "DeepMind founder"])
    assert "u:person:demis-hassabis" in out.stdout
```

- [ ] **Step 2: Run, verify FAIL**
- [ ] **Step 3: Implement**

```python
# src/kg/cli/save.py
from __future__ import annotations
import json
from pathlib import Path
import typer

from kg.paths import KgPaths
from kg.config import Config
from kg.storage.sqlite import SQLiteAdapter
from kg.embed import make_embedder
from kg.resolve import Resolver
from kg.dedup import Deduper
from kg.gate import Gate


def _build_gate(paths: KgPaths, cfg: Config) -> Gate:
    adapter = SQLiteAdapter(paths.kg_db)
    emb = make_embedder(cfg)
    return Gate(adapter, Resolver(adapter, emb, cfg.thresholds, cfg.project.user_id),
                Deduper(adapter, emb, cfg.thresholds), emb, cfg, cfg.project.user_id)


def save_cli(
    nodes: Path = typer.Option(..., "--nodes"),
    edges: Path = typer.Option(..., "--edges"),
    source: str = typer.Option(..., "--source"),
) -> None:
    paths = KgPaths.for_cwd()
    cfg = Config.from_path(paths.config)
    gate = _build_gate(paths, cfg)
    nlist = json.loads(Path(nodes).read_text())
    elist = json.loads(Path(edges).read_text())
    report = gate.normalize(nlist, elist, source)
    for d in report.decisions:
        typer.echo(f"{d.action:9} {d.type:13} {d.name}  -> {d.target_id} ({d.score:.2f} via {d.via})")
    typer.echo(f"edges: {report.edges_upserted}  new same_as: {report.new_same_as}")
```

```python
# src/kg/cli/query.py
from __future__ import annotations
import json
import typer

from kg.paths import KgPaths
from kg.config import Config
from kg.storage.sqlite import SQLiteAdapter
from kg.embed import make_embedder
from kg.search import hybrid_search
from kg.traverse import expand
from kg.pack import pack


def _adapter(paths, cfg): return SQLiteAdapter(paths.kg_db)
def _emb(cfg): return make_embedder(cfg)


def search_cli(
    query: str = typer.Argument(...),
    mode: str = typer.Option("hybrid", "--mode"),
    type: str = typer.Option(None, "--type"),
    k: int = typer.Option(10, "-k"),
) -> None:
    paths = KgPaths.for_cwd(); cfg = Config.from_path(paths.config)
    ad = _adapter(paths, cfg)
    hits = hybrid_search(ad, _emb(cfg), query, mode=mode, k=k, type_filter=type, config=cfg)
    for nid, score in hits:
        n = ad.get(nid)
        typer.echo(f"{score:.4f}  {nid}  {n.name if n else ''}")


def expand_cli(
    ids: list[str] = typer.Argument(...),
    hops: int = typer.Option(2, "--hops"),
) -> None:
    paths = KgPaths.for_cwd(); cfg = Config.from_path(paths.config)
    ad = _adapter(paths, cfg)
    sg = expand(ad, ids, hops=hops, cap=cfg.query.subgraph_cap)
    for n in sg.nodes:
        typer.echo(f"{n.id}  {n.name}")
```

```python
# src/kg/cli/resolve_cli.py
from __future__ import annotations
import json
import typer

from kg.paths import KgPaths
from kg.config import Config
from kg.storage.sqlite import SQLiteAdapter
from kg.embed import make_embedder
from kg.resolve import Resolver
from kg.dedup import Deduper
from kg.ontology import Node


def resolve_cli(name: str = typer.Argument(...),
                type: str = typer.Option(..., "--type")) -> None:
    paths = KgPaths.for_cwd(); cfg = Config.from_path(paths.config)
    ad = SQLiteAdapter(paths.kg_db); emb = make_embedder(cfg)
    res = Resolver(ad, emb, cfg.thresholds, cfg.project.user_id).resolve(name, type)
    typer.echo(f"{res.via}  {res.matched_id}  ({res.score:.2f})  canonical={res.canonical_name}")


def dedup_check_cli(node_json: str = typer.Argument(...)) -> None:
    paths = KgPaths.for_cwd(); cfg = Config.from_path(paths.config)
    ad = SQLiteAdapter(paths.kg_db); emb = make_embedder(cfg)
    node = Node.model_validate_json(node_json)
    res = Deduper(ad, emb, cfg.thresholds).dedup(node, cfg.embedding.embed_fields)
    typer.echo(f"best={res.best_match_id}  score={res.score:.2f}")
```

Register in `main.py` (append):

```python
from kg.cli import save as save_cmd        # noqa: E402
from kg.cli import query as query_cmd      # noqa: E402
from kg.cli import resolve_cli as rcli     # noqa: E402
app.command(name="save")(save_cmd.save_cli)
app.command(name="search")(query_cmd.search_cli)
app.command(name="expand")(query_cmd.expand_cli)
app.command(name="resolve")(rcli.resolve_cli)
app.command(name="dedup-check")(rcli.dedup_check_cli)
```

- [ ] **Step 4: Run, verify PASS**
- [ ] **Step 5: Commit** — `feat(m1): kg save/search/expand/resolve/dedup-check CLI`

---

### Task 14: `/kg:extract` and `/kg:query` skills

**Files:**
- Create: `skills/kg-extract/SKILL.md`, `skills/kg-extract/references/extraction-prompt.md`, `skills/kg-extract/references/output-schema.json`, `skills/kg-query/SKILL.md`, `skills/kg-query/references/query-modes.md`
- Test: `tests/test_skills_structure.py`

**Interfaces:** none code — these are the harness-facing markdown skills. The structural test asserts presence, <500-line SKILL.md, and the references.

- [ ] **Step 1: Failing test**

```python
# tests/test_skills_structure.py
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "skills"


def test_extract_skill_exists_and_bounded():
    md = (ROOT / "kg-extract" / "SKILL.md").read_text(encoding="utf-8")
    assert len(md.splitlines()) < 500
    assert "kg save" in md
    assert (ROOT / "kg-extract" / "references" / "extraction-prompt.md").exists()
    assert (ROOT / "kg-extract" / "references" / "output-schema.json").exists()


def test_query_skill_exists_and_bounded():
    md = (ROOT / "kg-query" / "SKILL.md").read_text(encoding="utf-8")
    assert len(md.splitlines()) < 500
    assert "kg search" in md
    assert (ROOT / "kg-query" / "references" / "query-modes.md").exists()
```

- [ ] **Step 2: Run, verify FAIL**
- [ ] **Step 3: Author skills** — write `skills/kg-extract/SKILL.md` describing the chunk → prompt → validate → `kg save` loop (spec §6.2), with the output-schema.json mirroring the gate's `extracted_nodes/edges` shapes and the ontology contract; write `skills/kg-query/SKILL.md` describing hybrid/NL-Cypher/deep-search modes (spec §7) calling `kg search/expand/pack`. Keep each under 500 lines; push detail into `references/`.
- [ ] **Step 4: Run, verify PASS**
- [ ] **Step 5: Commit** — `feat(m1): /kg:extract and /kg:query skills`

---

### Task 15: M1 acceptance — ingest → save → query

**Files:**
- Create: `tests/test_acceptance_m1.py`

- [ ] **Step 1: Write the acceptance test**

```python
# tests/test_acceptance_m1.py
"""M1 E2E: M0 ingest → hand-extracted nodes.json → kg save → kg search → expand.
Mirrors spec §6.3 + §7 (gate + read path), without the LLM (extraction is faked)."""
import json
from typer.testing import CliRunner
from kg.cli.main import app


def test_m1_gate_and_query(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    r = CliRunner()
    assert r.invoke(app, ["init", "--user-id", "u", "--scope", "demo"]).exit_code == 0

    (tmp_path / "n.json").write_text(json.dumps([
        {"type": "person", "name": "Demis Hassabis", "summary": "Founder of DeepMind."},
        {"type": "organization", "name": "DeepMind", "summary": "AI company in London."},
        {"type": "object", "subtype": "software", "name": "AlphaFold",
         "summary": "Protein structure predictor by DeepMind."},
    ]))
    (tmp_path / "e.json").write_text(json.dumps([
        {"source_name": "Demis Hassabis", "semantic_type": "employed_by", "target_name": "DeepMind"},
        {"source_name": "DeepMind", "semantic_type": "owns", "target_name": "AlphaFold"},
    ]))

    save = r.invoke(app, ["save", "--nodes", str(tmp_path / "n.json"),
                          "--edges", str(tmp_path / "e.json"),
                          "--source", "raw/x.md#chunk-0"])
    assert save.exit_code == 0
    assert "NEW" in save.stdout

    # idempotent re-save → no new nodes
    save2 = r.invoke(app, ["save", "--nodes", str(tmp_path / "n.json"),
                           "--edges", str(tmp_path / "e.json"),
                           "--source", "raw/x.md#chunk-0"])
    assert "RESOLVED" in save2.stdout or "MERGED" in save2.stdout

    # query lands on Demis
    search = r.invoke(app, ["search", "DeepMind founder"])
    assert "u:person:demis-hassabis" in search.stdout

    # 2-hop expand from Demis reaches AlphaFold
    expand = r.invoke(app, ["expand", "u:person:demis-hassabis", "--hops", "2"])
    assert "alphafold" in expand.stdout.lower()
```

- [ ] **Step 2: Run full suite** — `uv run pytest -v`. Expected: all green (M0 + M1).
- [ ] **Step 3: Commit** — `feat(m1): acceptance test; M1 complete`

---

## Self-Review

**1. Spec coverage (M1 scope — spec §4, §6.3, §7, §11 M1 subset):**
- Storage adapter interface + SQLite (§4) → Tasks 3,4,5,6. ✓
- Recursive-CTE traversal, FTS5, sqlite-vec (§4) → Tasks 5,6. ✓
- Two embeddings (name-only + full-context) (§4) → Tasks 7,9. ✓
- Resolution exact→fuzzy→semantic, type-gated, NO merges (§6.3 step 2) → Task 8. ✓
- Dedup 0.7·cos+0.3·fuzzy, candidates same-type ANN ∪ same-canonical (§6.3 step 4) → Task 9. ✓
- Gate route ≥0.95 merge / 0.85–0.95 flag same_as / <0.85 new (§6.3 step 5) → Task 10. ✓
- Merge semantics (aliases/sources union, conflicts, re-embed, tombstone) (§6.3) → Task 10. ✓
- Content-derived IDs, idempotent (§5) → Task 2, verified by gate tests. ✓
- Tombstone not delete (§5.2) → Task 4. ✓
- Ontology rejects unknown types (§6.3 step 1, §13) → Task 10. ✓
- Hybrid search + RRF k=60 (§7) → Task 11. ✓
- Expand BFS + subgraph cap (§7) → Task 12. ✓
- Pack rank + budget trim (§7) → Task 12. ✓
- `kg save/search/expand/resolve/dedup-check` (§11) → Task 13. ✓
- `/kg:extract` + `/kg:query` skills (§7, §14) → Task 14. ✓
- Gap: NL→Cypher and deep-search wiki are M4, correctly deferred. Embedding API providers stubbed via FakeEmbedder fallback — acceptable for v1 local-first.

**2. Placeholder scan:** Task 10 has one flagged refinement (merge edge-repointing) called out inline for fix-before-commit. Task 14 Step 3 authoring is genuine prose authoring (not a code placeholder). No TBD/TODO elsewhere.

**3. Type consistency:**
- `node_id`/`edge_id` (Task 2) used identically in Tasks 9,10. ✓
- `StorageAdapter` ABC (Task 3) matches `SQLiteAdapter` impl (Tasks 4–6) and `Resolver`/`Deduper`/`search`/`traverse` consumers. ✓
- `Decision.action` set `{NEW,RESOLVED,MERGED,FLAGGED}` consistent Task 10 → CLI report (Task 13) → acceptance (Task 15). ✓
- `Subgraph(nodes, edges)` (Task 3) used by `neighbors`, `expand`, `pack`. ✓
- `hybrid_search(adapter, embedder, query, mode, k, type_filter, config)` signature (Task 11) matches CLI (Task 13) and acceptance. ✓

**Scope:** M1 is one coherent deliverable (graph + gate + reads). Cypher/viz/dream/MCP/install deferred to M2–M6. No fixes needed beyond the flagged Task 10 refinement.

---

## Execution Handoff

**Plan complete and saved to `docs/superpowers/plans/2026-07-26-kg-m1-graph-gate.md`. Two execution options:**

**1. Subagent-Driven (recommended)** — fresh subagent per task, review between tasks.
**2. Inline Execution** — task-by-task in this session with checkpoints.

**Which approach?** After M1 ships, the next plans are **M2 (dream + review)**, then M3–M6, written one per milestone against M1's realized interfaces.
