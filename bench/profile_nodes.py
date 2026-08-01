"""Profile 100k node build by phase to find the real bottleneck."""
from __future__ import annotations
import tempfile, time
from pathlib import Path
from kg.config import Config
from kg.embed import FakeEmbedder
from kg.storage.sqlite import SQLiteAdapter
from kg.ontology import Node

N = 100_000
BATCH = 5000

def main():
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        adapter = SQLiteAdapter(tmp / "kg.db")
        emb = FakeEmbedder()

        # phase 1: build Node objects (python)
        t0 = time.perf_counter()
        all_nodes = []
        for i in range(N):
            all_nodes.append(Node(id=f"scale:person:p{i:06d}", type="person", name=f"Person {i}",
                                  summary=f"Synthetic person {i}", status="active",
                                  sources=[{"doc":"c","chunk":0}]))
        t_obj = time.perf_counter() - t0

        # phase 2: embeddings (FakeEmbedder deterministic)
        t0 = time.perf_counter()
        for n in all_nodes:
            n.embedding = emb.embed(n.name)
        t_emb = time.perf_counter() - t0

        # phase 3: upsert in batches
        t0 = time.perf_counter()
        for start in range(0, N, BATCH):
            adapter.upsert_nodes(all_nodes[start:start+BATCH])
        t_upsert = time.perf_counter() - t0

        # phase 3a: isolate nodes-table insert vs FTS vs vec via direct SQL
        adapter2 = SQLiteAdapter(tmp / "kg2.db")
        # nodes table only
        t0 = time.perf_counter()
        for start in range(0, N, BATCH):
            batch = all_nodes[start:start+BATCH]
            adapter2.conn.executemany(
                "INSERT INTO nodes(id,data,status,type,canonical_name) VALUES(?,?,?,?,?)",
                [(n.id, n.model_dump_json(), n.status, n.type, n.name) for n in batch])
        adapter2.conn.commit()
        t_nodes_only = time.perf_counter() - t0

        # nodes + FTS only
        adapter3 = SQLiteAdapter(tmp / "kg3.db")
        t0 = time.perf_counter()
        for start in range(0, N, BATCH):
            batch = all_nodes[start:start+BATCH]
            adapter3.conn.executemany(
                "INSERT INTO nodes(id,data,status,type,canonical_name) VALUES(?,?,?,?,?)",
                [(n.id, n.model_dump_json(), n.status, n.type, n.name) for n in batch])
            adapter3.conn.executemany(
                "INSERT INTO nodes_fts(node_id,name,summary,type) VALUES(?,?,?,?)",
                [(n.id, n.name, n.summary or "", n.type) for n in batch])
        adapter3.conn.commit()
        t_fts = time.perf_counter() - t0

        # nodes + vec only
        adapter4 = SQLiteAdapter(tmp / "kg4.db")
        import sqlite_vec
        t0 = time.perf_counter()
        for start in range(0, N, BATCH):
            batch = all_nodes[start:start+BATCH]
            adapter4.conn.executemany(
                "INSERT INTO nodes(id,data,status,type,canonical_name) VALUES(?,?,?,?,?)",
                [(n.id, n.model_dump_json(), n.status, n.type, n.name) for n in batch])
            for n in batch:
                adapter4.conn.execute("INSERT INTO nodes_vec(node_id,embedding) VALUES(?,?)",
                    (n.id, sqlite_vec.serialize_float32(n.embedding)))
        adapter4.conn.commit()
        t_vec = time.perf_counter() - t0

        print("="*50)
        print(f"100k NODE BUILD PHASE PROFILE")
        print("="*50)
        print(f"obj build:     {t_obj:6.1f}s")
        print(f"embeddings:    {t_emb:6.1f}s")
        print(f"full upsert:   {t_upsert:6.1f}s")
        print(f"--- isolated ---")
        print(f"nodes only:    {t_nodes_only:6.1f}s")
        print(f"nodes+FTS:     {t_fts:6.1f}s  (FTS delta: {t_fts-t_nodes_only:.1f}s)")
        print(f"nodes+vec:     {t_vec:6.1f}s  (vec delta: {t_vec-t_nodes_only:.1f}s)")

if __name__ == "__main__":
    main()
