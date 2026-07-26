from __future__ import annotations
import json
import sqlite3
import sqlite_vec
from contextlib import contextmanager
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
CREATE VIRTUAL TABLE IF NOT EXISTS nodes_vec USING vec0(
  node_id TEXT PRIMARY KEY, embedding FLOAT[384]
);
"""


class SQLiteAdapter(StorageAdapter):
    def __init__(self, db_path: Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        # isolation_level=None → autocommit on by default; we issue explicit
        # BEGIN/COMMIT/ROLLBACK inside `transaction()`. The reentrant `_in_txn`
        # flag tells upsert/delete to skip their own commit when called inside
        # an outer transaction.
        self._in_txn = False
        self.conn = sqlite3.connect(str(self.db_path), isolation_level=None)
        self.conn.row_factory = sqlite3.Row
        self.conn.enable_load_extension(True)
        sqlite_vec.load(self.conn)
        self.conn.enable_load_extension(False)
        self.conn.executescript(_SCHEMA)
        self.conn.execute("PRAGMA journal_mode=WAL")

    @contextmanager
    def transaction(self):
        if self._in_txn:
            # Nested call — let the outermost own BEGIN/COMMIT/ROLLBACK.
            yield
            return
        self.conn.execute("BEGIN IMMEDIATE")
        self._in_txn = True
        try:
            yield
            self.conn.execute("COMMIT")
        except BaseException:
            self.conn.execute("ROLLBACK")
            raise
        finally:
            self._in_txn = False

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
                "DELETE FROM nodes_fts WHERE node_id=?", (n.id,)
            )
            self.conn.execute(
                "INSERT INTO nodes_fts(node_id, name, summary, type) VALUES(?,?,?,?)",
                (n.id, n.name, n.summary or "", n.type),
            )
            self.conn.execute("DELETE FROM nodes_vec WHERE node_id=?", (n.id,))
            if n.embedding is not None and n.status == "active":
                self.conn.execute(
                    "INSERT INTO nodes_vec(node_id, embedding) VALUES (?, ?)",
                    (n.id, sqlite_vec.serialize_float32(n.embedding)),
                )
        if not self._in_txn:
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
            self.conn.execute("DELETE FROM nodes_vec WHERE node_id=?", (node_id,))
            self.conn.execute(
                "DELETE FROM edges WHERE source=? OR target=?", (node_id, node_id)
            )
        if not self._in_txn:
            self.conn.commit()

    def count(self) -> dict:
        n = self.conn.execute("SELECT COUNT(*) c FROM nodes").fetchone()["c"]
        e = self.conn.execute("SELECT COUNT(*) c FROM edges").fetchone()["c"]
        return {"nodes": n, "edges": e}

    # edges / neighbors implemented in this task
    def _edge_endpoints(self, edge_id: str) -> tuple[str, str, str]:
        # id format: {src}|{sem}|{tgt} — split with maxsplit=2 so a sem
        # containing '|' (none currently, but be safe) doesn't break parsing.
        src, sem, tgt = edge_id.split("|", 2)
        return src, sem, tgt

    def upsert_edges(self, edges: list[Edge]) -> int:
        for e in edges:
            src, sem, tgt = self._edge_endpoints(e.id or "")
            self.conn.execute(
                "INSERT INTO edges(id, source, target, semantic_type, data, status) "
                "VALUES(?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET "
                "source=excluded.source, target=excluded.target, "
                "semantic_type=excluded.semantic_type, data=excluded.data, "
                "status=excluded.status",
                (e.id, src, tgt, sem, e.model_dump_json(), e.status),
            )
        if not self._in_txn:
            self.conn.commit()
        return len(edges)

    def neighbors(self, ids: list[str], depth: int = 1,
                  direction: str = "both",
                  edge_types: list[str] | None = None) -> Subgraph:
        if not ids:
            return Subgraph()
        seeds_json = json.dumps(list(ids))

        # Recursive CTE: breadth-first reach over edges. `direction` filters
        # which endpoint we step out of. We collect (nid, d) pairs.
        # NOTE: builds placeholders dynamically — values are always bound via
        # `?`, never string-interpolated.
        if direction == "out":
            step_join = "e.source = r.nid"
            step_select = "e.target"
        elif direction == "in":
            step_join = "e.target = r.nid"
            step_select = "e.source"
        else:  # both
            step_join = "(e.source = r.nid OR e.target = r.nid)"
            step_select = "CASE WHEN e.source = r.nid THEN e.target ELSE e.source END"

        et_clause = ""
        params: list = [seeds_json, depth]
        if edge_types:
            et_clause = " AND e.semantic_type IN (%s)" % ",".join("?" * len(edge_types))
            params.extend(list(edge_types))

        sql = (
            "WITH RECURSIVE reach(nid, d) AS ("
            " SELECT value, 0 FROM json_each(?)"
            " UNION ALL"
            f" SELECT {step_select}, r.d + 1"
            f" FROM reach r JOIN edges e ON {step_join} AND e.status = 'active'"
            f" WHERE r.d < ?{et_clause}"
            ") SELECT DISTINCT r.nid FROM reach r JOIN nodes n ON n.id = r.nid "
            "WHERE r.d > 0 AND n.status != 'tombstoned'"
        )
        rows = self.conn.execute(sql, params).fetchall()
        reached = {r["nid"] for r in rows}

        nodes = [n for n in (self.get(nid) for nid in reached) if n and n.status != "tombstoned"]
        # Active edges among reached ∪ seeds.
        all_ids = set(ids) | reached
        placeholders = ",".join("?" * len(all_ids))
        erows = self.conn.execute(
            f"SELECT data FROM edges WHERE status = 'active' "
            f"AND source IN ({placeholders}) AND target IN ({placeholders})",
            [*all_ids, *all_ids],
        ).fetchall()
        edges = [Edge.model_validate_json(r["data"]) for r in erows]
        return Subgraph(nodes=nodes, edges=edges)

    def fts_search(self, query, k=10, type_filter=None):
        # BM25 rank from FTS5 — lower (more negative) = better match, so we
        # ORDER BY r ascending. Active nodes only.
        sql = (
            "SELECT n.id, bm25(nodes_fts) AS r FROM nodes_fts f "
            "JOIN nodes n ON n.id = f.node_id "
            "WHERE nodes_fts MATCH ? AND n.status = 'active'"
        )
        params: list = [query]
        if type_filter:
            sql += " AND json_extract(n.data, '$.type') = ?"
            params.append(type_filter)
        sql += " ORDER BY r LIMIT ?"
        params.append(k)
        rows = self.conn.execute(sql, params).fetchall()
        return [(r["id"], float(r["r"])) for r in rows]

    def vec_search(self, embedding, k=10, type_filter=None):
        # vec0 KNN can't pre-filter by type, so when a type_filter is given
        # we over-fetch (up to all active nodes) and filter/truncate in
        # Python — otherwise k wrong-type hits could crowd out a valid
        # same-type match ranked just past k.
        fetch_k = k
        if type_filter:
            fetch_k = max(k, self.count()["nodes"])
        rows = self.conn.execute(
            "SELECT node_id, distance FROM nodes_vec "
            "WHERE embedding MATCH ? AND k = ? "
            "ORDER BY distance",
            (sqlite_vec.serialize_float32(embedding), fetch_k),
        ).fetchall()
        out = []
        for r in rows:
            n = self.get(r["node_id"])
            if not n or n.status != "active":
                continue
            if type_filter and n.type != type_filter:
                continue
            score = 1.0 - float(r["distance"])  # distance → similarity-ish
            out.append((r["node_id"], score))
        # Ensure ordering: highest score (lowest distance) first.
        out.sort(key=lambda t: t[1], reverse=True)
        return out[:k]
