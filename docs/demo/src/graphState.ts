import type { GraphData, GraphEdge, GraphNode } from "./types";

export interface DisplaySettings {
  edgeBrightness: number;
  nodeGlow: number;
  bloom: number;
}

export interface GraphFilters {
  query: string;
  cluster: string;
  nodeTypes: ReadonlySet<string>;
  edgeTypes: ReadonlySet<string>;
}

export interface CameraTarget {
  lookAt: [number, number, number];
  position: [number, number, number];
}

export interface Connection {
  edge: GraphEdge;
  node: GraphNode;
  direction: "in" | "out";
}

export const DEFAULT_DISPLAY_SETTINGS: DisplaySettings = {
  edgeBrightness: 1,
  nodeGlow: 1,
  bloom: 1,
};

export const DISPLAY_LIMITS = {
  edgeBrightness: { min: 0.1, max: 3 },
  nodeGlow: { min: 0, max: 2 },
  bloom: { min: 0, max: 2 },
} as const;

export const STELLAR_LEGEND = [
  { type: "O", label: "Blue giant", range: "50+", color: "#80a0ff" },
  { type: "B", label: "Blue-white", range: "26–49", color: "#c0d0ff" },
  { type: "A", label: "White", range: "13–25", color: "#e8e8ff" },
  { type: "F", label: "Yellow-white", range: "7–12", color: "#fff0c0" },
  { type: "G", label: "Sun-like", range: "4–6", color: "#ffe080" },
  { type: "K", label: "Orange", range: "2–3", color: "#ffa060" },
  { type: "M", label: "Red dwarf", range: "0–1", color: "#ff6050" },
] as const;

const NODE_TYPE_COLORS: Record<string, string> = {
  project: "#e11d48",
  package: "#f97316",
  module: "#f97316",
  folder: "#22c55e",
  file: "#3b82f6",
  code_file: "#3b82f6",
  class: "#a855f7",
  interface: "#a855f7",
  function: "#06b6d4",
  method: "#06b6d4",
  route: "#eab308",
  variable: "#64748b",
};

const EDGE_TYPE_COLORS: Record<string, readonly [number, number, number]> = {
  calls: [0.11, 0.64, 0.49],
  imports: [0.23, 0.51, 0.96],
  part_of: [0.11, 0.52, 0.52],
  contains: [0.65, 0.33, 0.97],
  uses: [0.92, 0.7, 0.03],
};

export function normalizeType(value?: string): string {
  return (value || "unknown").trim().toLowerCase();
}

export function colorForNodeType(type?: string): string {
  return NODE_TYPE_COLORS[normalizeType(type)] ?? "#94a3b8";
}

export function colorForEdgeType(type?: string): readonly [number, number, number] {
  return EDGE_TYPE_COLORS[normalizeType(type)] ?? [0.11, 0.52, 0.52];
}

export function stellarForDegree(degree: number): (typeof STELLAR_LEGEND)[number] {
  if (degree >= 50) return STELLAR_LEGEND[0];
  if (degree >= 26) return STELLAR_LEGEND[1];
  if (degree >= 13) return STELLAR_LEGEND[2];
  if (degree >= 7) return STELLAR_LEGEND[3];
  if (degree >= 4) return STELLAR_LEGEND[4];
  if (degree >= 2) return STELLAR_LEGEND[5];
  return STELLAR_LEGEND[6];
}

export function countTypes(values: readonly string[]): Map<string, number> {
  const counts = new Map<string, number>();
  for (const value of values) {
    const key = normalizeType(value);
    counts.set(key, (counts.get(key) ?? 0) + 1);
  }
  return counts;
}

export function visibleNodeIds(data: GraphData, filters: GraphFilters): Set<number> {
  const q = filters.query.trim().toLowerCase();
  const visible = new Set<number>();
  for (const node of data.nodes) {
    const type = normalizeType(node.subtype);
    if (filters.cluster !== "all" && String(node.cluster) !== filters.cluster) continue;
    if (!filters.nodeTypes.has(type)) continue;
    if (q && !node.name.toLowerCase().includes(q) && !(node.qualified_name ?? "").toLowerCase().includes(q)) continue;
    visible.add(node.id);
  }
  return visible;
}

export function edgeIsVisible(edge: GraphEdge, visibleIds: ReadonlySet<number>, edgeTypes: ReadonlySet<string>): boolean {
  return visibleIds.has(edge.source) && visibleIds.has(edge.target) && edgeTypes.has(normalizeType(edge.type));
}

export function oneHopIds(data: GraphData, nodeId: number | null): Set<number> | null {
  if (nodeId === null) return null;
  const ids = new Set<number>([nodeId]);
  for (const id of data.adjacency.get(nodeId) ?? []) ids.add(id);
  return ids;
}

export function connectionsFor(data: GraphData, nodeId: number): Connection[] {
  const byId = new Map(data.nodes.map((node) => [node.id, node]));
  const result: Connection[] = [];
  for (const edge of data.edges) {
    if (edge.source === nodeId) {
      const node = byId.get(edge.target);
      if (node) result.push({ edge, node, direction: "out" });
    } else if (edge.target === nodeId) {
      const node = byId.get(edge.source);
      if (node) result.push({ edge, node, direction: "in" });
    }
  }
  return result;
}

export function cameraTarget(nodes: readonly GraphNode[]): CameraTarget | null {
  if (!nodes.length) return null;
  let x = 0, y = 0, z = 0;
  for (const node of nodes) { x += node.x; y += node.y; z += node.z; }
  x /= nodes.length; y /= nodes.length; z /= nodes.length;
  let spread = 0;
  for (const node of nodes) spread = Math.max(spread, Math.hypot(node.x - x, node.y - y, node.z - z));
  const distance = Math.max(nodes.length <= 5 ? 300 : 200, spread * 2.4);
  return { lookAt: [x, y, z], position: [x + distance * 0.2, y + distance * 0.15, z + distance] };
}

function clamp(key: keyof DisplaySettings, raw: unknown): number {
  const n = typeof raw === "number" ? raw : Number.NaN;
  if (!Number.isFinite(n)) return DEFAULT_DISPLAY_SETTINGS[key];
  return Math.min(DISPLAY_LIMITS[key].max, Math.max(DISPLAY_LIMITS[key].min, n));
}

export function clampDisplaySettings(raw: Partial<DisplaySettings>): DisplaySettings {
  return {
    edgeBrightness: clamp("edgeBrightness", raw.edgeBrightness),
    nodeGlow: clamp("nodeGlow", raw.nodeGlow),
    bloom: clamp("bloom", raw.bloom),
  };
}

export function loadDisplaySettings(storage: Pick<Storage, "getItem"> | null): DisplaySettings {
  try {
    const raw = storage?.getItem("unified-mem-graph-display");
    return raw ? clampDisplaySettings(JSON.parse(raw)) : DEFAULT_DISPLAY_SETTINGS;
  } catch {
    return DEFAULT_DISPLAY_SETTINGS;
  }
}
