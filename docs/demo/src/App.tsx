import { useEffect, useMemo, useState } from "react";
import { GraphScene } from "./scene/GraphScene";
import { transform } from "./layout";
import type { GraphData, GraphNode } from "./types";

interface RawGraph {
  source: string;
  nodes: { id: string; name: string; type?: string; subtype?: string; cluster: number; path?: string; summary?: string }[];
  edges: { source: string; target: string; type: string }[];
  clusters: { id: number; count: number }[];
  truncated_nodes: boolean;
  truncated_edges: boolean;
}

type Dataset = "real" | "stress";
const DATASETS: Record<Dataset, { file: string; label: string }> = {
  real: { file: "graph.json", label: "Real repo (1.7k)" },
  stress: { file: "graph-stress.json", label: "Stress 10k / 34k edges" },
};

// Highlight = hovered/selected node + its direct neighbors.
function neighborhoodSet(data: GraphData, focus: GraphNode | null): Set<number> | null {
  if (!focus) return null;
  const ids = new Set<number>([focus.id]);
  const nbrs = data.adjacency.get(focus.id);
  if (nbrs) for (const n of nbrs) ids.add(n);
  return ids;
}

export default function App() {
  const [dataset, setDataset] = useState<Dataset>("real");
  const [raw, setRaw] = useState<RawGraph | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  const [cluster, setCluster] = useState("all");
  const [hovered, setHovered] = useState<GraphNode | null>(null);
  const [selected, setSelected] = useState<GraphNode | null>(null);

  useEffect(() => {
    setRaw(null);
    setErr(null);
    setSelected(null);
    setHovered(null);
    fetch(DATASETS[dataset].file)
      .then((r) => r.json())
      .then(setRaw)
      .catch((e) => setErr(String(e)));
  }, [dataset]);

  const data: GraphData | null = useMemo(() => (raw ? transform(raw) : null), [raw]);

  // Filter highlight from search/cluster (dim everything not matching).
  const filterIds = useMemo(() => {
    if (!data) return null;
    const q = query.toLowerCase().trim();
    if (!q && cluster === "all") return null;
    const ids = new Set<number>();
    data.nodes.forEach((n) => {
      const inCluster = cluster === "all" || String(n.cluster) === cluster;
      const inQuery = !q || n.name.toLowerCase().includes(q) || (n.qualified_name ?? "").toLowerCase().includes(q);
      if (inCluster && inQuery) ids.add(n.id);
    });
    return ids;
  }, [data, query, cluster]);

  // Interaction highlight (hover or selected) takes priority over filter.
  const focus = hovered ?? selected;
  const interactionIds = useMemo(() => (data ? neighborhoodSet(data, focus) : null), [data, focus]);

  const highlightedIds = interactionIds ?? filterIds;

  if (err) return <div className="loading">Failed to load graph: {err}</div>;
  if (!data) return <div className="loading">Loading {DATASETS[dataset].label}…</div>;

  const topClusters = [...(raw?.clusters ?? [])].sort((a, b) => b.count - a.count).slice(0, 20);
  const focusNeighbors = focus ? (data.adjacency.get(focus.id)?.size ?? 0) : 0;

  return (
    <>
      <header>
        <h1>kg · unified-mem knowledge graph</h1>
        <p>
          {raw?.source} · {data.nodes.length.toLocaleString()} nodes · {data.edges.length.toLocaleString()} edges ·{" "}
          {raw?.clusters.length ?? 0} communities
          {raw?.truncated_nodes && " (truncated)"}
        </p>
      </header>

      <section className="controls">
        <select value={dataset} onChange={(e) => setDataset(e.target.value as Dataset)}>
          {Object.entries(DATASETS).map(([k, v]) => (
            <option key={k} value={k}>
              {v.label}
            </option>
          ))}
        </select>
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

      <GraphScene data={data} highlightedIds={highlightedIds} onHover={setHovered} onNodeClick={setSelected} />

      <div className="note">
        {focus ? (
          <>
            <b>{focus.name}</b> <small>({focus.subtype})</small>
            <br />
            cluster {focus.cluster} · degree {focus.deg} · {focusNeighbors} neighbor{focusNeighbors === 1 ? "" : "s"}
            {focus.qualified_name ? ` · ${focus.qualified_name}` : ""}
            {focus.summary ? <br /> : null}
            {focus.summary}
          </>
        ) : (
          "Drag to orbit · scroll/pinch to zoom · hover a node to highlight its neighbors · click for detail."
        )}
      </div>

      {selected && (
        <div className="detail-panel">
          <div className="detail-head">
            <strong>{selected.name}</strong>
            <button onClick={() => setSelected(null)} aria-label="Close">×</button>
          </div>
          <dl>
            <dt>Type</dt>
            <dd>{selected.subtype}</dd>
            <dt>Cluster</dt>
            <dd>{selected.cluster}</dd>
            <dt>Degree</dt>
            <dd>{selected.deg}</dd>
            <dt>Neighbors</dt>
            <dd>{focusNeighbors}</dd>
            {selected.qualified_name && (
              <>
                <dt>Path</dt>
                <dd className="mono">{selected.qualified_name}</dd>
              </>
            )}
            {selected.summary && (
              <>
                <dt>Summary</dt>
                <dd>{selected.summary}</dd>
              </>
            )}
          </dl>
        </div>
      )}

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
