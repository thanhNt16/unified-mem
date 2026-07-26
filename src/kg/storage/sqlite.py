from __future__ import annotations
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
        self.conn.execute("PRAGMA journal_mode=WAL")
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
                "DELETE FROM nodes_fts WHERE node_id=?", (n.id,)
            )
            self.conn.execute(
                "INSERT INTO nodes_fts(node_id, name, summary, type) VALUES(?,?,?,?)",
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
