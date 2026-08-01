from __future__ import annotations
import json
import re
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
  status TEXT NOT NULL DEFAULT 'active',
  type TEXT NOT NULL DEFAULT 'unknown',
  canonical_name TEXT NOT NULL DEFAULT ''
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
CREATE TABLE IF NOT EXISTS graph_meta(
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS node_clusters(
  node_id TEXT PRIMARY KEY,
  cluster_id INTEGER NOT NULL,
  generation INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_node_clusters_generation ON node_clusters(generation);
CREATE INDEX IF NOT EXISTS idx_nodes_status ON nodes(status);
CREATE INDEX IF NOT EXISTS idx_nodes_type_canonical ON nodes(type, canonical_name);
CREATE INDEX IF NOT EXISTS idx_edges_source ON edges(source);
CREATE INDEX IF NOT EXISTS idx_edges_target ON edges(target);
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
        self._migrate()
        self.conn.execute("PRAGMA journal_mode=WAL")

    def _migrate(self) -> None:
        """Add indexed lookup columns to pre-existing databases. Idempotent."""
        cols = {r["name"] for r in self.conn.execute("PRAGMA table_info(nodes)")}
        added = False
        if "type" not in cols:
            self.conn.execute(
                "ALTER TABLE nodes ADD COLUMN type TEXT NOT NULL DEFAULT 'unknown'"
            )
            added = True
        if "canonical_name" not in cols:
            self.conn.execute(
                "ALTER TABLE nodes ADD COLUMN canonical_name TEXT NOT NULL DEFAULT ''"
            )
            added = True
        if added:
            self.conn.execute(
                "UPDATE nodes SET type=COALESCE(json_extract(data,'$.type'),'unknown'), "
                "canonical_name=COALESCE(json_extract(data,'$.canonical_name'),"
                "json_extract(data,'$.name'),'')"
            )
        self.conn.execute("CREATE INDEX IF NOT EXISTS idx_nodes_status ON nodes(status)")
        self.conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_nodes_type_canonical "
            "ON nodes(type, canonical_name)"
        )
        self.conn.execute("CREATE INDEX IF NOT EXISTS idx_edges_source ON edges(source)")
        self.conn.execute("CREATE INDEX IF NOT EXISTS idx_edges_target ON edges(target)")

    def _bump_generation(self) -> None:
        """Increment graph mutation counter. No-op if no active rows change.

        Called from upsert_nodes/upsert_edges when at least one active row
        is touched. Cheap: 2 statements on a single-row table.
        """
        self.conn.execute(
            "INSERT OR IGNORE INTO graph_meta(key, value) VALUES('generation', '1')"
        )
        self.conn.execute(
            "UPDATE graph_meta SET value=CAST(value AS INTEGER)+1 WHERE key='generation'"
        )

    def generation(self) -> int:
        """Current graph generation (monotonic). 1 if no mutations yet."""
        row = self.conn.execute(
            "SELECT value FROM graph_meta WHERE key='generation'"
        ).fetchone()
        return int(row["value"]) if row else 1

    def get_clusters(self, generation: int) -> dict[str, int] | None:
        """Return cached node→cluster mapping for ``generation``, or None."""
        rows = self.conn.execute(
            "SELECT node_id, cluster_id FROM node_clusters WHERE generation=?",
            (generation,),
        ).fetchall()
        return {r["node_id"]: r["cluster_id"] for r in rows} or None

    def store_clusters(self, mapping: dict[str, int], generation: int) -> None:
        """Replace cached cluster mapping for ``generation``.

        One DELETE clears stale rows for prior generations; INSERT fills new.
        Wrapped in a transaction so the cache is never partially populated.
        """
        with self.transaction():
            self.conn.execute("DELETE FROM node_clusters")
            self.conn.executemany(
                "INSERT INTO node_clusters(node_id, cluster_id, generation) VALUES(?,?,?)",
                [(nid, cid, generation) for nid, cid in mapping.items()],
            )

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
        if not nodes:
            return 0
        if any(n.status == "active" for n in nodes):
            self._bump_generation()
        # One transaction for the whole batch. The reentrant `transaction()`
        # context manager lets an outer caller (e.g. Gate.normalize) wrap a
        # large batch in a single BEGIN/COMMIT — this is the fix for the
        # 500k-autocommit bottleneck at 100k nodes.
        with self.transaction():
            ids = [n.id for n in nodes]
            # 1. nodes table — executemany (single statement, batched bind)
            self.conn.executemany(
                "INSERT INTO nodes(id, data, status, type, canonical_name) VALUES(?,?,?,?,?) "
                "ON CONFLICT(id) DO UPDATE SET data=excluded.data, status=excluded.status, "
                "type=excluded.type, canonical_name=excluded.canonical_name",
                [
                    (n.id, self._dump(n), n.status, n.type, n.canonical_name or n.name or "")
                    for n in nodes
                ],
            )
            # 2. FTS — `node_id` is UNINDEXED, so DELETE-by-node_id scans the
            # whole FTS table. Only delete IDs that already existed (updates);
            # pure inserts skip deletion entirely. This turns 100k-node bulk
            # loads from O(N^2) FTS churn into O(N) inserts.
            placeholders = ",".join("?" * len(ids))
            existing = {
                r["node_id"]
                for r in self.conn.execute(
                    f"SELECT node_id FROM nodes_fts WHERE node_id IN ({placeholders})",
                    ids,
                ).fetchall()
            }
            if existing:
                ep = ",".join("?" * len(existing))
                self.conn.execute(
                    f"DELETE FROM nodes_fts WHERE node_id IN ({ep})", list(existing)
                )
            self.conn.executemany(
                "INSERT INTO nodes_fts(node_id, name, summary, type) VALUES(?,?,?,?)",
                [(n.id, n.name, n.summary or "", n.type) for n in nodes],
            )
            # 3. vec0 — DELETE then INSERT for active nodes with embeddings.
            # vec0 virtual tables do not support executemany reliably across
            # builds, so we keep per-row inserts but they share the one
            # transaction (no autocommit storm).
            active_vec = [
                (n.id, sqlite_vec.serialize_float32(n.embedding))
                for n in nodes
                if n.embedding is not None and n.status == "active"
            ]
            for n in nodes:
                self.conn.execute("DELETE FROM nodes_vec WHERE node_id=?", (n.id,))
            for nid, emb in active_vec:
                self.conn.execute(
                    "INSERT INTO nodes_vec(node_id, embedding) VALUES (?, ?)", (nid, emb)
                )
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
        if not edges:
            return 0
        if any(e.status == "active" for e in edges):
            self._bump_generation()
        rows = []
        for e in edges:
            src, sem, tgt = self._edge_endpoints(e.id or "")
            rows.append((e.id, src, tgt, sem, e.model_dump_json(), e.status))
        with self.transaction():
            self.conn.executemany(
                "INSERT INTO edges(id, source, target, semantic_type, data, status) "
                "VALUES(?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET "
                "source=excluded.source, target=excluded.target, "
                "semantic_type=excluded.semantic_type, data=excluded.data, "
                "status=excluded.status",
                rows,
            )
        return len(edges)

    def neighbors(self, ids: list[str], depth: int = 1,
                  direction: str = "both",
                  edge_types: list[str] | None = None,
                  max_nodes: int = 500) -> Subgraph:
        """Reach `ids` within a total `max_nodes` traversal work budget.

        `max_nodes` caps distinct CTE rows, including seeds. This is a work
        budget, unlike `expand(cap=...)`, which is presentation-only.
        `UNION` deduplicates cyclic paths; the recursive `LIMIT` prevents
        dense/hub traversal from materializing unbounded paths.
        """
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

        # `UNION` on a recursive compound SELECT enforces distinct
        # (nid, d) rows so cycles can never enlarge the queue, and a `LIMIT`
        # inside the recursive leg makes the row budget a hard cap even
        # before the outer SELECT runs. Seed rows count toward the total.
        max_nodes = max(max_nodes, 0)
        et_clause = ""
        params: list = [seeds_json, depth]
        if edge_types:
            et_clause = " AND e.semantic_type IN (%s)" % ",".join("?" * len(edge_types))
            params.extend(list(edge_types))
        params.append(len(ids) + max_nodes)

        sql = (
            "WITH RECURSIVE reach(nid, d) AS ("
            " SELECT value, 0 FROM json_each(?)"
            " UNION"
            f" SELECT {step_select}, r.d + 1"
            f" FROM reach r JOIN edges e ON {step_join} AND e.status = 'active'"
            f" WHERE r.d < ?{et_clause}"
            f" LIMIT ?"
            ") SELECT DISTINCT r.nid FROM reach r WHERE r.d > 0"
        )
        rows = self.conn.execute(sql, params).fetchall()
        reached = {r["nid"] for r in rows}

        nodes = [n for n in (self.get(nid) for nid in reached) if n and n.status != "tombstoned"]
        # Active edges among reached ∪ seeds.
        all_ids = set(ids) | reached
        if not all_ids:
            return Subgraph(nodes=nodes, edges=[])
        placeholders = ",".join("?" * len(all_ids))
        erows = self.conn.execute(
            f"SELECT data FROM edges WHERE status = 'active' "
            f"AND source IN ({placeholders}) AND target IN ({placeholders})",
            [*all_ids, *all_ids],
        ).fetchall()
        edges = [Edge.model_validate_json(r["data"]) for r in erows]
        return Subgraph(nodes=nodes, edges=edges)

    @staticmethod
    def _escape_fts_query(query: str) -> str:
        """Normalize user text into literal FTS5 terms."""
        if not isinstance(query, str):
            raise ValueError("query must be a non-empty string")
        terms = re.findall(r"[^\W_]+", query, flags=re.UNICODE)
        if not terms:
            raise ValueError("query must contain at least one searchable term")
        return " AND ".join(f'"{term}"' for term in terms)

    def fts_search(self, query, k=10, type_filter=None):
        # BM25 rank from FTS5 — lower (more negative) = better match, so we
        # ORDER BY r ascending. Active nodes only.
        sql = (
            "SELECT n.id, bm25(nodes_fts) AS r FROM nodes_fts f "
            "JOIN nodes n ON n.id = f.node_id "
            "WHERE nodes_fts MATCH ? AND n.status = 'active'"
        )
        params: list = [self._escape_fts_query(query)]
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
