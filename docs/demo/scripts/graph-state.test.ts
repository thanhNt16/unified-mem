import { test } from "node:test";
import assert from "node:assert/strict";
import {
  DEFAULT_DISPLAY_SETTINGS,
  type GraphFilters,
  cameraTarget,
  clampDisplaySettings,
  colorForEdgeType,
  colorForNodeType,
  connectionsFor,
  countTypes,
  edgeIsVisible,
  loadDisplaySettings,
  normalizeType,
  oneHopIds,
  stellarForDegree,
  visibleNodeIds,
} from "../src/graphState.ts";
import type { GraphData, GraphEdge, GraphNode } from "../src/types.ts";

function node(id: number, opts: Partial<GraphNode> = {}): GraphNode {
  return {
    id,
    x: opts.x ?? id,
    y: opts.y ?? 0,
    z: opts.z ?? 0,
    label: String(id),
    name: `node${id}`,
    sourceId: `s${id}`,
    size: 1,
    color: "#fff",
    cluster: opts.cluster ?? 1,
    subtype: opts.subtype ?? "function",
    deg: opts.deg ?? 0,
    ...opts,
  };
}

function edge(source: number, target: number, type = "calls"): GraphEdge {
  return { source, target, type };
}

function graph(nodes: GraphNode[], edges: GraphEdge[]): GraphData {
  const adjacency = new Map<number, Set<number>>();
  for (const e of edges) {
    if (!adjacency.has(e.source)) adjacency.set(e.source, new Set());
    if (!adjacency.has(e.target)) adjacency.set(e.target, new Set());
    adjacency.get(e.source)!.add(e.target);
    adjacency.get(e.target)!.add(e.source);
  }
  return { nodes, edges, adjacency };
}

const allFilters = (extra: Partial<GraphFilters> = {}): GraphFilters => ({
  query: "",
  cluster: "all",
  nodeTypes: new Set(["function", "class", "code_file", "file"]),
  edgeTypes: new Set(["calls", "imports", "part_of", "contains", "uses"]),
  ...extra,
});

test("normalizeType lowercases and trims", () => {
  assert.equal(normalizeType("  Function "), "function");
  assert.equal(normalizeType(undefined), "unknown");
});

test("colorForNodeType returns configured color then default", () => {
  assert.equal(colorForNodeType("Function"), "#06b6d4");
  assert.equal(colorForNodeType("code_file"), "#3b82f6");
  assert.equal(colorForNodeType("nope"), "#94a3b8");
});

test("colorForEdgeType returns configured tuple then default", () => {
  assert.deepEqual([...colorForEdgeType("Calls")], [0.11, 0.64, 0.49]);
  assert.deepEqual([...colorForEdgeType("unknown")], [0.11, 0.52, 0.52]);
});

test("stellarForDegree boundaries", () => {
  assert.equal(stellarForDegree(50).type, "O");
  assert.equal(stellarForDegree(49).type, "B");
  assert.equal(stellarForDegree(26).type, "B");
  assert.equal(stellarForDegree(25).type, "A");
  assert.equal(stellarForDegree(13).type, "A");
  assert.equal(stellarForDegree(12).type, "F");
  assert.equal(stellarForDegree(7).type, "F");
  assert.equal(stellarForDegree(6).type, "G");
  assert.equal(stellarForDegree(4).type, "G");
  assert.equal(stellarForDegree(3).type, "K");
  assert.equal(stellarForDegree(2).type, "K");
  assert.equal(stellarForDegree(1).type, "M");
  assert.equal(stellarForDegree(0).type, "M");
});

test("countTypes normalizes mixed casing", () => {
  const counts = countTypes(["Function", "function", "Class", ""]);
  assert.equal(counts.get("function"), 2);
  assert.equal(counts.get("class"), 1);
  assert.equal(counts.get("unknown"), 1);
});

test("visibleNodeIds applies query + cluster + node-type filters together", () => {
  const data = graph(
    [node(1, { name: "alpha", cluster: 1, subtype: "function" }), node(2, { name: "beta", cluster: 2, subtype: "class" }), node(3, { name: "gamma", cluster: 1, subtype: "function" })],
    [],
  );
  const visible = visibleNodeIds(data, allFilters({ query: "a", cluster: "1", nodeTypes: new Set(["function"]) }));
  assert.deepEqual([...visible].sort((a, b) => a - b), [1, 3]);
});

test("visibleNodeIds matches qualified_name too", () => {
  const data = graph([node(1, { name: "x", qualified_name: "src/alpha.py" })], []);
  const visible = visibleNodeIds(data, allFilters({ query: "alpha" }));
  assert.deepEqual([...visible], [1]);
});

test("edgeIsVisible false when an endpoint is filtered out", () => {
  const visible = new Set<number>([1]);
  assert.equal(edgeIsVisible(edge(1, 2, "calls"), visible, allFilters().edgeTypes), false);
  assert.equal(edgeIsVisible(edge(1, 1, "calls"), visible, allFilters().edgeTypes), true);
});

test("edgeIsVisible false when edge type filtered out", () => {
  const visible = new Set<number>([1, 2]);
  const edgeTypes = new Set(["part_of"]);
  assert.equal(edgeIsVisible(edge(1, 2, "calls"), visible, edgeTypes), false);
});

test("oneHopIds includes the focused node itself", () => {
  const data = graph([node(1), node(2), node(3)], [edge(1, 2), edge(1, 3)]);
  assert.deepEqual([...oneHopIds(data, 1)!].sort((a, b) => a - b), [1, 2, 3]);
  assert.equal(oneHopIds(data, null), null);
});

test("connectionsFor classifies inbound vs outbound", () => {
  const data = graph([node(1), node(2), node(3)], [edge(1, 2, "calls"), edge(3, 1, "imports")]);
  const conns = connectionsFor(data, 1).sort((a, b) => a.direction.localeCompare(b.direction));
  assert.equal(conns.length, 2);
  const inbound = conns.find((c) => c.direction === "in")!;
  const outbound = conns.find((c) => c.direction === "out")!;
  assert.equal(inbound.node.id, 3);
  assert.equal(outbound.node.id, 2);
});

test("connectionsFor ignores edges to absent nodes", () => {
  const data = graph([node(1)], [edge(1, 99, "calls")]);
  assert.equal(connectionsFor(data, 1).length, 0);
});

test("cameraTarget empty, singleton, neighborhood", () => {
  assert.equal(cameraTarget([]), null);
  const single = cameraTarget([node(1, { x: 5, y: 5, z: 5 })]);
  assert.deepEqual([...single!.lookAt], [5, 5, 5]);
  assert.ok(single!.position[2] >= 200, "min distance floor honored");
  const hood = cameraTarget([node(1, { x: 0 }), node(2, { x: 100 })]);
  assert.ok(hood!.position[2] >= 200, "neighborhood distance meets min");
});

test("clampDisplaySettings clamps out-of-range and invalid", () => {
  assert.deepEqual(clampDisplaySettings({ edgeBrightness: 99, nodeGlow: -5, bloom: NaN }), {
    edgeBrightness: 3,
    nodeGlow: 0,
    bloom: DEFAULT_DISPLAY_SETTINGS.bloom,
  });
  assert.deepEqual(clampDisplaySettings({ edgeBrightness: 2, nodeGlow: 0.5, bloom: 1.5 }), {
    edgeBrightness: 2,
    nodeGlow: 0.5,
    bloom: 1.5,
  });
});

test("loadDisplaySettings falls back on bad JSON and missing storage", () => {
  assert.deepEqual(loadDisplaySettings(null), DEFAULT_DISPLAY_SETTINGS);
  const memory: Record<string, string> = { "unified-mem-graph-display": "{bad" };
  const storage = { getItem: (k: string) => memory[k] ?? null };
  assert.deepEqual(loadDisplaySettings(storage), DEFAULT_DISPLAY_SETTINGS);
  memory["unified-mem-graph-display"] = JSON.stringify({ edgeBrightness: 2, nodeGlow: 2, bloom: 2 });
  assert.deepEqual(loadDisplaySettings(storage), { edgeBrightness: 2, nodeGlow: 2, bloom: 2 });
});
