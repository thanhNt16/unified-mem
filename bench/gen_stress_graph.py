"""Generate a deterministic synthetic 10k-node dense graph for stress-testing
the 3D demo. The real repo index has ~1.7k nodes; this proves the renderer
holds at 10k nodes with dense edges. Output matches
ui/graph-ui/snapshots/demo-source.json schema.
"""
from __future__ import annotations
import json, random
from pathlib import Path

CLUSTERS = 200
PER_CLUSTER = 50          # 200 * 50 = 10_000 nodes
INTRA_CALLS = 120         # dense function->function edges per cluster
NODES = CLUSTERS * PER_CLUSTER

FUNC_VERBS = ["parse", "render", "fetch", "commit", "flush", "merge", "scan", "emit", "bind", "eval"]
NOUNS = ["node", "edge", "cache", "queue", "token", "layer", "batch", "hook", "route", "span"]


def main():
    rng = random.Random(20260801)
    nodes, edges = [], []
    for c in range(CLUSTERS):
        file_id = f"c{c}/mod.py"
        nodes.append({"id": file_id, "name": f"mod.py", "type": "object",
                      "subtype": "code_file", "summary": "", "path": f"cluster_{c}/mod.py", "cluster": c})
        func_ids = []
        for i in range(PER_CLUSTER - 1):
            fid = f"c{c}/mod.py#f{i}"
            func_ids.append(fid)
            name = f"{rng.choice(FUNC_VERBS)}_{rng.choice(NOUNS)}_{i}"
            nodes.append({"id": fid, "name": name, "type": "object",
                          "subtype": "function", "summary": "", "path": f"cluster_{c}/mod.py", "cluster": c})
            edges.append({"source": fid, "target": file_id, "type": "part_of"})
        # dense intra-cluster call edges (random function->function)
        for _ in range(INTRA_CALLS):
            a, b = rng.sample(func_ids, 2)
            edges.append({"source": a, "target": b, "type": "calls"})
        # a few cross-cluster edges so the galaxy isn't fully disconnected
        if c > 0 and rng.random() < 0.6:
            other = rng.randint(0, c - 1)
            edges.append({"source": rng.choice(func_ids),
                          "target": f"c{other}/mod.py#f{rng.randrange(PER_CLUSTER - 1)}",
                          "type": "imports"})

    counts = {c: sum(1 for n in nodes if n["cluster"] == c) for c in range(CLUSTERS)}
    payload = {
        "source": "synthetic stress dataset (10k nodes, deterministic seed)",
        "nodes": nodes,
        "edges": edges,
        "clusters": [{"id": c, "count": counts[c]} for c in range(CLUSTERS)],
        "truncated_nodes": False,
        "truncated_edges": False,
    }
    out = Path("ui/graph-ui/snapshots/stress-source.json")
    out.write_text(json.dumps(payload, separators=(",", ":")), encoding="utf-8")
    print(f"nodes={len(nodes)} edges={len(edges)} clusters={CLUSTERS} bytes={out.stat().st_size}")


if __name__ == "__main__":
    main()
