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

function neighborhoodSet(data: GraphData, focus: GraphNode | null): Set<number> | null {
  if (!focus) return null;
  const ids = new Set<number>([focus.id]);
  const nbrs = data.adjacency.get(focus.id);
  if (nbrs) for (const n of nbrs) ids.add(n);
  return ids;
}

export default function App() {
  const [dataset, setDataset] = useState<Dataset>("real");
  const [retryGen, setRetryGen] = useState(0);
  const [raw, setRaw] = useState<RawGraph | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  const [cluster, setCluster] = useState("all");
  const [hovered, setHovered] = useState<GraphNode | null>(null);
  const [selected, setSelected] = useState<GraphNode | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    setRaw(null);
    setErr(null);
    setSelected(null);
    setHovered(null);
    fetch(DATASETS[dataset].file, { signal: controller.signal })
      .then((r) => {
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        return r.json();
      })
      .then(setRaw)
      .catch((e) => {
        if (e.name === "AbortError") return; // superseded by a newer request
        setErr(String(e));
      });
    return () => controller.abort();
  }, [dataset, retryGen]);

  const data: GraphData | null = useMemo(() => (raw ? transform(raw) : null), [raw]);

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

  const focus = hovered ?? selected;
  const interactionIds = useMemo(() => (data ? neighborhoodSet(data, focus) : null), [data, focus]);
  const highlightedIds = interactionIds ?? filterIds;

  // All clusters, sorted by size; native <select> handles ~200 options.
  const clusters = useMemo(
    () => [...(raw?.clusters ?? [])].sort((a, b) => b.count - a.count),
    [raw],
  );

  const clearFocus = () => {
    setHovered(null);
    setSelected(null);
  };

  const focusNeighbors = focus ? (data?.adjacency.get(focus.id)?.size ?? 0) : 0;

  if (err) {
    return (
      <div className="loading">
        Failed to load {DATASETS[dataset].label}: {err}
        <br />
        <button onClick={() => setRetryGen((g) => g + 1)}>Retry</button>
      </div>
    );
  }
  if (!data) return <div className="loading">Loading {DATASETS[dataset].label}…</div>;

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
          {clusters.map((c) => (
            <option key={c.id} value={c.id}>
              Cluster {c.id} · {c.count}
            </option>
          ))}
        </select>
        <button
          onClick={() => {
            setQuery("");
            setCluster("all");
            clearFocus();
          }}
        >
          Reset
        </button>
      </section>

      <GraphScene
        data={data}
        highlightedIds={highlightedIds}
        onHover={setHovered}
        onNodeClick={setSelected}
        onBackgroundClick={clearFocus}
      />

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
            <button onClick={clearFocus} aria-label="Close">×</button>
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
