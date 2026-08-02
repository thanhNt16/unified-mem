// Client-side 3D layout. CBM computes layout3d server-side in C; our demo has
// only cluster ids + a single part_of hierarchy, so we place clusters on
// spheres and fan members out. Good enough for a static visualization demo.
import type { GraphData, GraphNode, GraphEdge } from "./types";
import { normalizeType, stellarForDegree } from "./graphState";

interface RawNode {
  id: string;
  name: string;
  type?: string;
  subtype?: string;
  cluster: number;
  path?: string;
  summary?: string;
}

interface RawGraph {
  nodes: RawNode[];
  edges: { source: string; target: string; type: string }[];
  clusters: { id: number; count: number }[];
}

export function transform(raw: RawGraph): GraphData {
  // Group nodes by cluster, assign each cluster a point on a Fibonacci sphere.
  const byCluster = new Map<number, RawNode[]>();
  for (const n of raw.nodes) {
    const arr = byCluster.get(n.cluster) ?? [];
    arr.push(n);
    byCluster.set(n.cluster, arr);
  }
  const clusters = [...byCluster.keys()].sort((a, b) => a - b);
  const clusterCenter = new Map<number, [number, number, number]>();
  const N = clusters.length;
  const R = 240 * Math.sqrt(Math.max(N, 1)); // galaxy radius scales with cluster count
  clusters.forEach((cid, i) => {
    const phi = Math.acos(1 - (2 * (i + 0.5)) / N);
    const theta = Math.PI * (1 + Math.sqrt(5)) * i;
    clusterCenter.set(cid, [R * Math.sin(phi) * Math.cos(theta), R * Math.sin(phi) * Math.sin(theta), R * Math.cos(phi)]);
  });

  // String id -> numeric id, degree counter.
  const idIndex = new Map<string, number>();
  const degByNode = new Map<string, number>();
  for (const e of raw.edges) {
    degByNode.set(e.source, (degByNode.get(e.source) ?? 0) + 1);
    degByNode.set(e.target, (degByNode.get(e.target) ?? 0) + 1);
  }

  const nodes: GraphNode[] = [];
  const clusterIndex = new Map<number, number>();
  raw.nodes.forEach((n, i) => {
    idIndex.set(n.id, i);
    const [cx, cy, cz] = clusterCenter.get(n.cluster) ?? [0, 0, 0];
    // Local sphere: fan members around the cluster center.
    const localIndex = clusterIndex.get(n.cluster) ?? 0;
    clusterIndex.set(n.cluster, localIndex + 1);
    const local = fibPoint(localIndex, byCluster.get(n.cluster)!.length, 40);
    const deg = degByNode.get(n.id) ?? 0;
    nodes.push({
      id: i,
      sourceId: n.id,
      x: cx + local[0],
      y: cy + local[1],
      z: cz + local[2],
      label: n.name,
      name: n.name,
      qualified_name: n.path,
      summary: n.summary,
      size: 1.5 + Math.min(6, Math.sqrt(deg) * 1.1),
      color: stellarForDegree(deg).color,
      cluster: n.cluster,
      subtype: normalizeType(n.subtype ?? n.type),
      deg,
    });
  });

  const edges: GraphEdge[] = [];
  const adjacency = new Map<number, Set<number>>();
  for (const e of raw.edges) {
    const source = idIndex.get(e.source);
    const target = idIndex.get(e.target);
    if (source === undefined || target === undefined) continue;
    edges.push({ source, target, type: normalizeType(e.type) });
    const sourceNeighbors = adjacency.get(source) ?? new Set<number>();
    sourceNeighbors.add(target);
    adjacency.set(source, sourceNeighbors);
    const targetNeighbors = adjacency.get(target) ?? new Set<number>();
    targetNeighbors.add(source);
    adjacency.set(target, targetNeighbors);
  }

  return { nodes, edges, adjacency };
}

function fibPoint(i: number, total: number, radius: number): [number, number, number] {
  const k = i + 0.5;
  const phi = Math.acos(1 - (2 * k) / total);
  const theta = Math.PI * (1 + Math.sqrt(5)) * k;
  return [radius * Math.sin(phi) * Math.cos(theta), radius * Math.sin(phi) * Math.sin(theta), radius * Math.cos(phi)];
}
