import { useEffect, useMemo, useState } from "react";
import { ErrorBoundary } from "./ErrorBoundary";
import { DisplaySettingsMenu, FilterPanel, NodeDetailPanel, StatsPanel } from "./GraphPanels";
import {
  DEFAULT_DISPLAY_SETTINGS,
  type DisplaySettings,
  type GraphFilters,
  cameraTarget,
  countTypes,
  edgeIsVisible,
  loadDisplaySettings,
  normalizeType,
  oneHopIds,
  visibleNodeIds,
} from "./graphState";
import { transform } from "./layout";
import { GraphScene } from "./scene/GraphScene";
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
type Tab = "graph" | "stats";

const DATASETS: Record<Dataset, { file: string; label: string }> = {
  real: { file: "graph.json", label: "Real repository · 1.7k" },
  stress: { file: "graph-stress.json", label: "Stress · 10k / 34k" },
};

export default function App() {
  const [dataset, setDataset] = useState<Dataset>("real");
  const [tab, setTab] = useState<Tab>("graph");
  const [retryGeneration, setRetryGeneration] = useState(0);
  const [cameraGeneration, setCameraGeneration] = useState(0);
  const [raw, setRaw] = useState<RawGraph | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [hovered, setHovered] = useState<GraphNode | null>(null);
  const [selected, setSelected] = useState<GraphNode | null>(null);
  const [filters, setFilterState] = useState<GraphFilters>({
    query: "",
    cluster: "all",
    nodeTypes: new Set(),
    edgeTypes: new Set(),
  });
  const [showLabels, setShowLabels] = useState(true);
  const [display, setDisplay] = useState<DisplaySettings>(() => {
    try { return loadDisplaySettings(window.localStorage); } catch { return DEFAULT_DISPLAY_SETTINGS; }
  });

  useEffect(() => {
    const controller = new AbortController();
    setRaw(null);
    setError(null);
    setSelected(null);
    setHovered(null);
    fetch(DATASETS[dataset].file, { signal: controller.signal })
      .then((response) => {
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        return response.json();
      })
      .then(setRaw)
      .catch((cause) => {
        if (cause.name === "AbortError") return;
        setError(String(cause));
      });
    return () => controller.abort();
  }, [dataset, retryGeneration]);

  const data: GraphData | null = useMemo(() => (raw ? transform(raw) : null), [raw]);
  const nodeTypeCounts = useMemo(
    () => countTypes(data?.nodes.map((node) => node.subtype ?? "unknown") ?? []),
    [data],
  );
  const edgeTypeCounts = useMemo(
    () => countTypes(data?.edges.map((edge) => edge.type) ?? []),
    [data],
  );

  useEffect(() => {
    setFilterState({
      query: "",
      cluster: "all",
      nodeTypes: new Set(nodeTypeCounts.keys()),
      edgeTypes: new Set(edgeTypeCounts.keys()),
    });
    setShowLabels(dataset === "real");
  }, [dataset, nodeTypeCounts, edgeTypeCounts]);

  const visibleIds = useMemo(
    () => data ? visibleNodeIds(data, filters) : new Set<number>(),
    [data, filters],
  );
  const focus = hovered ?? selected;
  const focusedIds = useMemo(() => (data ? oneHopIds(data, focus?.id ?? null) : null), [data, focus]);
  const overviewTarget = useMemo(() => (data ? cameraTarget(data.nodes) : null), [data]);
  const target = useMemo(() => {
    if (!data || !selected) return overviewTarget;
    const ids = oneHopIds(data, selected.id) ?? new Set([selected.id]);
    return cameraTarget(data.nodes.filter((node) => ids.has(node.id)));
  }, [data, selected, overviewTarget]);
  const visibleEdgeCount = useMemo(
    () => data?.edges.reduce((count, edge) => count + (edgeIsVisible(edge, visibleIds, filters.edgeTypes) ? 1 : 0), 0) ?? 0,
    [data, visibleIds, filters.edgeTypes],
  );

  const clusters = useMemo(() => [...(raw?.clusters ?? [])].sort((a, b) => b.count - a.count), [raw]);
  const filtered = data ? visibleIds.size !== data.nodes.length || visibleEdgeCount !== data.edges.length : false;

  const updateFilters = (next: Partial<GraphFilters>) => setFilterState((current) => ({ ...current, ...next }));
  const clearSelection = () => { setHovered(null); setSelected(null); };
  const selectNode = (node: GraphNode) => { setHovered(null); setSelected(node); setCameraGeneration((n) => n + 1); };
  const reset = () => {
    clearSelection();
    setFilterState({
      query: "",
      cluster: "all",
      nodeTypes: new Set(nodeTypeCounts.keys()),
      edgeTypes: new Set(edgeTypeCounts.keys()),
    });
    setShowLabels(dataset === "real");
    setCameraGeneration((n) => n + 1);
  };
  const updateDisplay = (next: DisplaySettings) => {
    setDisplay(next);
    try { window.localStorage.setItem("unified-mem-graph-display", JSON.stringify(next)); } catch { /* storage is optional */ }
  };

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") clearSelection();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  if (error) {
    return (
      <div className="loading">
        Failed to load {DATASETS[dataset].label}: {error}
        <br />
        <button onClick={() => setRetryGeneration((generation) => generation + 1)}>Retry</button>
      </div>
    );
  }
  if (!data || !raw) return <div className="loading">Loading {DATASETS[dataset].label}…</div>;

  return (
    <ErrorBoundary>
      <div className="app-shell">
        <header className="app-header">
          <div className="brand">
            <strong>kg</strong>
            <span>unified memory graph</span>
          </div>
          <nav className="tabs" aria-label="View">
            <button className={tab === "graph" ? "tab active" : "tab"} onClick={() => setTab("graph")}>Graph</button>
            <button className={tab === "stats" ? "tab active" : "tab"} onClick={() => setTab("stats")}>Stats</button>
          </nav>
          <div className="header-actions">
            <select value={dataset} onChange={(event) => setDataset(event.target.value as Dataset)} aria-label="Dataset">
              {Object.entries(DATASETS).map(([key, value]) => <option key={key} value={key}>{value.label}</option>)}
            </select>
            {tab === "graph" && <DisplaySettingsMenu settings={display} onChange={updateDisplay} />}
          </div>
        </header>

        {tab === "stats" ? (
          <main className="stats-main">
            <StatsPanel
              data={data}
              source={raw.source}
              truncated={raw.truncated_nodes || raw.truncated_edges}
              nodeTypeCounts={nodeTypeCounts}
              edgeTypeCounts={edgeTypeCounts}
            />
          </main>
        ) : (
          <main className={`graph-layout ${selected ? "has-detail" : ""}`}>
            <aside className="sidebar">
              <FilterPanel
                filters={filters}
                setFilters={updateFilters}
                nodeTypeCounts={nodeTypeCounts}
                edgeTypeCounts={edgeTypeCounts}
                clusters={clusters}
                showLabels={showLabels}
                setShowLabels={setShowLabels}
                onReset={reset}
              />
            </aside>
            <section className="graph-main">
              <div className="hud">
                <span>{visibleIds.size.toLocaleString()} nodes</span>
                <span>{visibleEdgeCount.toLocaleString()} edges</span>
                {filtered && <span className="hud-muted">of {data.nodes.length.toLocaleString()} / {data.edges.length.toLocaleString()}</span>}
                {focusedIds && <span>{focusedIds.size} focused</span>}
                {(raw.truncated_nodes || raw.truncated_edges) && <span className="warn">truncated</span>}
              </div>
              <GraphScene
                data={data}
                visibleIds={visibleIds}
                focusedIds={focusedIds}
                selectedId={selected?.id ?? null}
                hovered={hovered}
                cameraTarget={target}
                cameraGeneration={cameraGeneration}
                display={display}
                edgeTypes={filters.edgeTypes}
                showLabels={showLabels}
                onHover={(node) => { if (!node || visibleIds.has(node.id)) setHovered(node); }}
                onNodeClick={selectNode}
                onBackgroundClick={clearSelection}
              />
              <div className="graph-help">
                {focus ? <><b>{focus.name}</b> · {normalizeType(focus.subtype)} · cluster {focus.cluster} · degree {focus.deg}</> : "Drag to orbit · scroll to zoom · hover for neighbors · click to focus"}
              </div>
              <footer className="attribution">
                Renderer adapted from <a href="https://github.com/DeusData/codebase-memory-mcp" target="_blank" rel="noreferrer">codebase-memory-mcp</a> (MIT, DeusData).
              </footer>
            </section>
            {selected && <NodeDetailPanel node={selected} data={data} onClose={clearSelection} onSelect={selectNode} />}
          </main>
        )}
      </div>
    </ErrorBoundary>
  );
}
