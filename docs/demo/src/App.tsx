import { useEffect, useMemo, useState } from "react";
import { GraphScene } from "./scene/GraphScene";
import { transform } from "./layout";
import type { GraphData, GraphNode } from "./types";

interface RawGraph {
  source: string;
  nodes: { id: string; name: string; type?: string; subtype?: string; cluster: number; path?: string }[];
  edges: { source: string; target: string; type: string }[];
  clusters: { id: number; count: number }[];
  truncated_nodes: boolean;
  truncated_edges: boolean;
}

export default function App() {
  const [raw, setRaw] = useState<RawGraph | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  const [cluster, setCluster] = useState("all");
  const [selected, setSelected] = useState<GraphNode | null>(null);

  useEffect(() => {
    fetch("graph.json")
      .then((r) => r.json())
      .then(setRaw)
      .catch((e) => setErr(String(e)));
  }, []);

  const data: GraphData | null = useMemo(() => (raw ? transform(raw) : null), [raw]);

  const highlightedIds = useMemo(() => {
    if (!data) return null;
    const q = query.toLowerCase().trim();
    if (!q && cluster === "all" && !selected) return null;
    const ids = new Set<number>();
    data.nodes.forEach((n) => {
      const inCluster = cluster === "all" || String(n.cluster) === cluster;
      const inQuery = !q || n.name.toLowerCase().includes(q) || (n.qualified_name ?? "").toLowerCase().includes(q);
      if (inCluster && inQuery) ids.add(n.id);
    });
    return ids;
  }, [data, query, cluster, selected]);

  if (err) return <div className="loading">Failed to load graph: {err}</div>;
  if (!data) return <div className="loading">Loading real graph data…</div>;

  const topClusters = [...(raw?.clusters ?? [])].sort((a, b) => b.count - a.count).slice(0, 20);

  return (
    <>
      <header>
        <h1>kg · unified-mem knowledge graph</h1>
        <p>
          Real graph from <code>thanhNt16/unified-mem/.kg/kg.db</code> · {data.nodes.length.toLocaleString()} nodes ·{" "}
          {data.edges.length.toLocaleString()} edges · {raw?.clusters.length ?? 0} communities
          {raw?.truncated_nodes && " (truncated)"}
        </p>
      </header>

      <section className="controls">
        <input value={query} onChange={(e) => setQuery(e.target.value)} placeholder="Search nodes…" autoComplete="off" />
        <select value={cluster} onChange={(e) => setCluster(e.target.value)}>
          <option value="all">All clusters</option>
          {topClusters.map((c) => (
            <option key={c.id} value={c.id}>
              Cluster {c.id} · {c.count}
            </option>
          ))}
        </select>
        <button
          onClick={() => {
            setQuery("");
            setCluster("all");
            setSelected(null);
          }}
        >
          Reset
        </button>
      </section>

      <GraphScene data={data} highlightedIds={highlightedIds} onNodeClick={setSelected} />

      <div className="note">
        {selected ? (
          <>
            <b>{selected.name}</b> <small>({selected.subtype})</small>
            <br />
            cluster {selected.cluster} · degree {selected.deg}
            {selected.qualified_name ? ` · ${selected.qualified_name}` : ""}
          </>
        ) : (
          "Drag to orbit · scroll/pinch to zoom · tap a node for detail."
        )}
      </div>

      <footer className="attribution">
        3D renderer adapted from{" "}
        <a href="https://github.com/DeusData/codebase-memory-mcp" target="_blank" rel="noreferrer">
          codebase-memory-mcp
        </a>{" "}
        (MIT, DeusData).
      </footer>
    </>
  );
}
