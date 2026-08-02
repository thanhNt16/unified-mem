"""Generate a deterministic synthetic 10k-node / 15k-edge dense graph for the
3D demo. 200 clusters x 50 nodes (1 module + 49 functions). Edges are exactly:

  9,800 ``part_of``  (49 functions -> module, per cluster)
  5,200 ``calls``    (26 unique function pairs per cluster, enumerate i<j)
  = 15,000 edges

No ``random``: fully deterministic, zero self-loops, zero duplicate triples.
Output matches ``ui/graph-ui/snapshots/demo-source.json`` schema and is the
Pages demo source (see ``.github/workflows/pages.yml``).
"""
from __future__ import annotations
import json
from pathlib import Path

CLUSTERS = 200
PER_CLUSTER = 50          # 200 * 50 = 10_000 nodes
INTRA_CALLS = 26          # unique function->function calls per cluster
NODES = CLUSTERS * PER_CLUSTER  # 10_000
PART_OF = (PER_CLUSTER - 1) * CLUSTERS      # 9_800
EDGES = PART_OF + INTRA_CALLS * CLUSTERS    # 15_000

FUNC_VERBS = ["parse", "render", "fetch", "commit", "flush", "merge", "scan", "emit", "bind", "eval"]
NOUNS = ["node", "edge", "cache", "queue", "token", "layer", "batch", "hook", "route", "span"]


def main():
    nodes: list[dict] = []
    edges: list[dict] = []
    for c in range(CLUSTERS):
        file_id = f"c{c}/mod.py"
        nodes.append({"id": file_id, "name": "mod.py", "type": "object",
                      "subtype": "code_file", "summary": "", "path": f"cluster_{c}/mod.py", "cluster": c})
        func_ids: list[str] = []
        for i in range(PER_CLUSTER - 1):
            fid = f"c{c}/mod.py#f{i}"
            func_ids.append(fid)
            name = f"{FUNC_VERBS[i % len(FUNC_VERBS)]}_{NOUNS[(i // len(FUNC_VERBS)) % len(NOUNS)]}_{i}"
            nodes.append({"id": fid, "name": name, "type": "object",
                          "subtype": "function", "summary": "", "path": f"cluster_{c}/mod.py", "cluster": c})
            edges.append({"source": fid, "target": file_id, "type": "part_of"})
        # dense intra-cluster call edges: first INTRA_CALLS unique (i<j) pairs
        added = 0
        for i in range(len(func_ids)):
            if added >= INTRA_CALLS:
                break
            for j in range(i + 1, len(func_ids)):
                edges.append({"source": func_ids[i], "target": func_ids[j], "type": "calls"})
                added += 1
                if added >= INTRA_CALLS:
                    break

    # Deterministic self-check: exact counts, no self-loops, no duplicate triples.
    assert len(nodes) == NODES, f"expected {NODES} nodes, got {len(nodes)}"
    assert len(edges) == EDGES, f"expected {EDGES} edges, got {len(edges)}"
    seen: set[tuple[str, str, str]] = set()
    for e in edges:
        assert e["source"] != e["target"], f"self-loop: {e}"
        key = (e["source"], e["target"], e["type"])
        assert key not in seen, f"duplicate edge: {e}"
        seen.add(key)

    counts = {c: sum(1 for n in nodes if n["cluster"] == c) for c in range(CLUSTERS)}
    payload = {
        "source": "synthetic stress dataset (10k nodes, 15k edges, deterministic)",
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
