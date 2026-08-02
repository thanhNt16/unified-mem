import { useEffect, useRef, useState } from "react";
import type { GraphData, GraphNode } from "./types";
import {
  DEFAULT_DISPLAY_SETTINGS,
  DISPLAY_LIMITS,
  STELLAR_LEGEND,
  type Connection,
  type DisplaySettings,
  type GraphFilters,
  colorForEdgeType,
  colorForNodeType,
  connectionsFor,
  normalizeType,
  stellarForDegree,
} from "./graphState";

/* ── Filter panel ─────────────────────────────────────────────── */

interface FilterProps {
  filters: GraphFilters;
  setFilters: (next: Partial<GraphFilters>) => void;
  nodeTypeCounts: ReadonlyMap<string, number>;
  edgeTypeCounts: ReadonlyMap<string, number>;
  clusters: readonly { id: number; count: number }[];
  showLabels: boolean;
  setShowLabels: (value: boolean) => void;
  onReset: () => void;
}

function TypeChip({ label, color, count, active, onClick }: { label: string; color: string; count: number; active: boolean; onClick: () => void }) {
  return (
    <button className={`chip ${active ? "chip-on" : ""}`} onClick={onClick} aria-pressed={active}>
      <span className="chip-dot" style={{ backgroundColor: color }} />
      <span className="chip-label">{label}</span>
      <span className="chip-count">{count}</span>
    </button>
  );
}

export function FilterPanel({ filters, setFilters, nodeTypeCounts, edgeTypeCounts, clusters, showLabels, setShowLabels, onReset }: FilterProps) {
  const toggleSet = (set: ReadonlySet<string>, value: string): Set<string> => {
    const next = new Set(set);
    if (next.has(value)) next.delete(value);
    else next.add(value);
    return next;
  };
  const allNodeTypes = [...nodeTypeCounts.entries()].sort((a, b) => b[1] - a[1]);
  const allEdgeTypes = [...edgeTypeCounts.entries()].sort((a, b) => b[1] - a[1]);

  return (
    <div className="panel">
      <div className="panel-title">Filters</div>
      <input
        className="search"
        value={filters.query}
        onChange={(e) => setFilters({ query: e.target.value })}
        placeholder="Search nodes…"
        autoComplete="off"
      />
      <select className="cluster-select" value={filters.cluster} onChange={(e) => setFilters({ cluster: e.target.value })}>
        <option value="all">All clusters</option>
        {clusters.map((c) => (
          <option key={c.id} value={c.id}>
            Cluster {c.id} · {c.count}
          </option>
        ))}
      </select>

      <div className="chip-group">
        <div className="chip-head">
          <span>Node types</span>
          <button className="link" onClick={() => setFilters({ nodeTypes: new Set(allNodeTypes.map(([t]) => t)) })}>All</button>
          <button className="link" onClick={() => setFilters({ nodeTypes: new Set() })}>None</button>
        </div>
        {allNodeTypes.map(([type, count]) => (
          <TypeChip
            key={type}
            label={type}
            color={colorForNodeType(type)}
            count={count}
            active={filters.nodeTypes.has(type)}
            onClick={() => setFilters({ nodeTypes: toggleSet(filters.nodeTypes, type) })}
          />
        ))}
      </div>

      <div className="chip-group">
        <div className="chip-head">
          <span>Edge types</span>
          <button className="link" onClick={() => setFilters({ edgeTypes: new Set(allEdgeTypes.map(([t]) => t)) })}>All</button>
          <button className="link" onClick={() => setFilters({ edgeTypes: new Set() })}>None</button>
        </div>
        {allEdgeTypes.map(([type, count]) => (
          <TypeChip
            key={type}
            label={type}
            color={`rgb(${colorForEdgeType(type).map((c) => Math.round(c * 255)).join(",")})`}
            count={count}
            active={filters.edgeTypes.has(type)}
            onClick={() => setFilters({ edgeTypes: toggleSet(filters.edgeTypes, type) })}
          />
        ))}
      </div>

      <label className="check">
        <input type="checkbox" checked={showLabels} onChange={(e) => setShowLabels(e.target.checked)} />
        Show labels
      </label>

      <details className="legend">
        <summary>Stellar degree legend</summary>
        {STELLAR_LEGEND.map((row) => (
          <div key={row.type} className="legend-row">
            <span className="chip-dot" style={{ backgroundColor: row.color }} />
            <span className="legend-type">{row.type} · {row.label}</span>
            <span className="legend-range">{row.range}</span>
          </div>
        ))}
      </details>

      <button className="reset" onClick={onReset}>Reset</button>
    </div>
  );
}

/* ── Display settings popover ─────────────────────────────────── */

interface DisplayProps {
  settings: DisplaySettings;
  onChange: (next: DisplaySettings) => void;
}

export function DisplaySettingsMenu({ settings, onChange }: DisplayProps) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") setOpen(false); };
    document.addEventListener("mousedown", onDown);
    document.addEventListener("keydown", onKey);
    return () => { document.removeEventListener("mousedown", onDown); document.removeEventListener("keydown", onKey); };
  }, [open]);

  const slider = (key: keyof DisplaySettings, label: string) => {
    const { min, max } = DISPLAY_LIMITS[key];
    return (
      <label className="slider-row">
        <span className="slider-label">{label}</span>
        <input
          type="range"
          min={min}
          max={max}
          step={0.1}
          value={settings[key]}
          onChange={(e) => onChange({ ...settings, [key]: Number(e.target.value) })}
        />
        <span className="slider-value">{settings[key].toFixed(1)}</span>
      </label>
    );
  };

  return (
    <div className="display" ref={ref}>
      <button className="ghost" aria-haspopup="dialog" aria-expanded={open} onClick={() => setOpen((v) => !v)}>Display</button>
      {open && (
        <div className="popover" role="dialog" aria-label="Display settings">
          {slider("edgeBrightness", "Edge brightness")}
          {slider("nodeGlow", "Node glow")}
          {slider("bloom", "Bloom")}
          <button className="link" onClick={() => onChange(DEFAULT_DISPLAY_SETTINGS)}>Reset to defaults</button>
        </div>
      )}
    </div>
  );
}

/* ── Stats panel ──────────────────────────────────────────────── */

interface StatsProps {
  data: GraphData;
  source: string;
  truncated: boolean;
  nodeTypeCounts: ReadonlyMap<string, number>;
  edgeTypeCounts: ReadonlyMap<string, number>;
}

function Distribution({ title, counts, total }: { title: string; counts: ReadonlyMap<string, number>; total: number }) {
  const rows = [...counts.entries()].sort((a, b) => b[1] - a[1]);
  if (!rows.length) return null;
  return (
    <div className="stat-block">
      <div className="stat-title">{title}</div>
      {rows.map(([type, count]) => (
        <div key={type} className="stat-row">
          <span className="stat-label">{type}</span>
          <span className="bar"><span className="bar-fill" style={{ width: `${(count / total) * 100}%` }} /></span>
          <span className="stat-value">{count}</span>
        </div>
      ))}
    </div>
  );
}

export function StatsPanel({ data, source, truncated, nodeTypeCounts, edgeTypeCounts }: StatsProps) {
  const avgDegree = data.nodes.length ? (data.edges.length * 2) / data.nodes.length : 0;
  const stellar = new Map<string, number>();
  for (const node of data.nodes) {
    const cls = stellarForDegree(node.deg).type;
    stellar.set(cls, (stellar.get(cls) ?? 0) + 1);
  }
  const communities = new Set(data.nodes.map((n) => n.cluster)).size;

  return (
    <div className="stats">
      <div className="stat-block">
        <div className="stat-title">Dataset</div>
        <div className="stat-row"><span className="stat-label">Source</span><span className="stat-value">{source}</span></div>
        <div className="stat-row"><span className="stat-label">Nodes</span><span className="stat-value">{data.nodes.length.toLocaleString()}</span></div>
        <div className="stat-row"><span className="stat-label">Edges</span><span className="stat-value">{data.edges.length.toLocaleString()}</span></div>
        <div className="stat-row"><span className="stat-label">Communities</span><span className="stat-value">{communities}</span></div>
        <div className="stat-row"><span className="stat-label">Avg degree</span><span className="stat-value">{avgDegree.toFixed(2)}</span></div>
        {truncated && <div className="stat-row"><span className="stat-label">Note</span><span className="stat-value">dataset truncated</span></div>}
      </div>
      <Distribution title="Node types" counts={nodeTypeCounts} total={data.nodes.length} />
      <Distribution title="Edge types" counts={edgeTypeCounts} total={data.edges.length} />
      <Distribution title="Stellar degree" counts={stellar} total={data.nodes.length} />
    </div>
  );
}

/* ── Node detail panel ────────────────────────────────────────── */

interface DetailProps {
  node: GraphNode;
  data: GraphData;
  onClose: () => void;
  onSelect: (node: GraphNode) => void;
}

const MAX_PER_GROUP = 25;

export function NodeDetailPanel({ node, data, onClose, onSelect }: DetailProps) {
  const connections = connectionsFor(data, node.id);
  const inbound = connections.filter((c) => c.direction === "in");
  const outbound = connections.filter((c) => c.direction === "out");
  const byType = (list: Connection[]) => {
    const groups = new Map<string, Connection[]>();
    for (const conn of list) {
      const type = normalizeType(conn.edge.type);
      if (!groups.has(type)) groups.set(type, []);
      groups.get(type)!.push(conn);
    }
    return [...groups.entries()].sort((a, b) => b[1].length - a[1].length);
  };

  const renderGroup = (title: string, list: Connection[]) => {
    if (!list.length) return null;
    const groups = byType(list);
    return (
      <div className="detail-section">
        <div className="detail-section-title">{title} · {list.length}</div>
        {groups.map(([type, conns]) => (
          <div key={type} className="detail-group">
            <div className="detail-group-title">
              <span className="chip-dot" style={{ backgroundColor: `rgb(${colorForEdgeType(type).map((c) => Math.round(c * 255)).join(",")})` }} />
              {type}
            </div>
            {conns.slice(0, MAX_PER_GROUP).map((conn) => (
              <button key={`${conn.edge.source}-${conn.edge.target}-${conn.node.id}`} className="neighbor" onClick={() => onSelect(conn.node)}>
                <span className="chip-dot" style={{ backgroundColor: colorForNodeType(conn.node.subtype) }} />
                <span className="neighbor-name">{conn.node.name}</span>
              </button>
            ))}
            {conns.length > MAX_PER_GROUP && <div className="more">+{conns.length - MAX_PER_GROUP} more</div>}
          </div>
        ))}
      </div>
    );
  };

  return (
    <aside className="detail-panel">
      <div className="detail-head">
        <div className="detail-title">
          <span className="chip-dot" style={{ backgroundColor: colorForNodeType(node.subtype) }} />
          <strong>{node.name}</strong>
          <small>{node.subtype}</small>
        </div>
        <button className="close" onClick={onClose} aria-label="Close">×</button>
      </div>
      <dl className="detail-meta">
        <dt>Cluster</dt><dd>{node.cluster}</dd>
        <dt>Degree</dt><dd>{node.deg}</dd>
        <dt>In</dt><dd>{inbound.length}</dd>
        <dt>Out</dt><dd>{outbound.length}</dd>
        {node.qualified_name && <><dt>Path</dt><dd className="mono">{node.qualified_name}</dd></>}
        {node.summary && <><dt>Summary</dt><dd>{node.summary}</dd></>}
      </dl>
      {renderGroup("Outbound", outbound)}
      {renderGroup("Inbound", inbound)}
    </aside>
  );
}
