"""Export real kg graph to docs/demo/graph.json for the live demo."""
from __future__ import annotations
import json
from pathlib import Path
from kg.storage.sqlite import SQLiteAdapter
from kg.community import louvain

MAX_NODES = 2000
MAX_EDGES = 4000

def main():
    a = SQLiteAdapter(Path(".kg/kg.db"))
    clusters = louvain(a)
    n_rows = a.conn.execute(
        "SELECT id, data, type, canonical_name FROM nodes WHERE status='active' ORDER BY id LIMIT ?",
        (MAX_NODES + 1,),
    ).fetchall()
    truncated_nodes = len(n_rows) > MAX_NODES
    n_rows = n_rows[:MAX_NODES]
    active_ids = {r["id"] for r in n_rows}
    e_rows = a.conn.execute(
        "SELECT source, target, semantic_type FROM edges WHERE status='active' "
        "AND source IN (SELECT id FROM nodes WHERE status='active') "
        "AND target IN (SELECT id FROM nodes WHERE status='active') "
        "ORDER BY id LIMIT ?",
        (MAX_EDGES + 1,),
    ).fetchall()
    truncated_edges = len(e_rows) > MAX_EDGES
    e_rows = e_rows[:MAX_EDGES]

    nodes = []
    for r in n_rows:
        try:
            data = json.loads(r["data"])
        except Exception:
            data = {}
        nodes.append({
            "id": r["id"],
            "name": data.get("name") or r["id"],
            "type": r["type"] or data.get("type", ""),
            "subtype": data.get("subtype"),
            "summary": (data.get("summary") or "")[:120],
            "path": (data.get("attributes") or {}).get("path", ""),
            "cluster": clusters.get(r["id"], -1),
        })
    edges = [{"source": r["source"], "target": r["target"], "type": r["semantic_type"]} for r in e_rows]
    cluster_ids = sorted(set(clusters.values()))
    payload = {
        "source": "thanhNt16/unified-mem .kg/kg.db",
        "nodes": nodes,
        "edges": edges,
        "clusters": [{"id": c, "count": sum(1 for n in nodes if n["cluster"] == c)} for c in cluster_ids],
        "truncated_nodes": truncated_nodes,
        "truncated_edges": truncated_edges,
    }
    out = Path("ui/graph-ui/snapshots/demo-source.json")
    out.write_text(json.dumps(payload, separators=(",", ":")), encoding="utf-8")
    print(f"nodes={len(nodes)} edges={len(edges)} clusters={len(cluster_ids)} bytes={out.stat().st_size}")
    a.conn.close()

if __name__ == "__main__":
    main()
