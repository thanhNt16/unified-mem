// Adapted from DeusData/codebase-memory-mcp graph-ui (MIT).
import { useMemo } from "react";
import * as THREE from "three";
import type { GraphNode, GraphEdge } from "../types";
import { edgeIntensityScale } from "./density";

interface Props {
  nodes: GraphNode[];
  edges: GraphEdge[];
  highlightedIds: Set<number> | null;
}

export function EdgeLines({ nodes, edges, highlightedIds }: Props) {
  const geometry = useMemo(() => {
    const densityScale = edgeIntensityScale(edges.length);
    const idToIdx = new Map<number, number>();
    for (let i = 0; i < nodes.length; i++) idToIdx.set(nodes[i].id, i);

    const hasHighlight = highlightedIds && highlightedIds.size > 0;
    const positions = new Float32Array(edges.length * 6);
    const colors = new Float32Array(edges.length * 6);
    let valid = 0;

    for (const edge of edges) {
      const si = idToIdx.get(edge.source);
      const ti = idToIdx.get(edge.target);
      if (si === undefined || ti === undefined) continue;

      const s = nodes[si];
      const t = nodes[ti];

      const sHL = !hasHighlight || highlightedIds.has(s.id);
      const tHL = !hasHighlight || highlightedIds.has(t.id);
      if (hasHighlight && !sHL && !tHL) continue;

      // Intra-cluster edges glow stronger than cross-cluster.
      const sameCluster = s.cluster === t.cluster;
      let intensity = sameCluster ? 0.22 : 0.07;
      if (hasHighlight) intensity = sHL && tHL ? 0.5 : 0.04 * densityScale;
      else intensity *= densityScale;

      const off = valid * 6;
      positions[off] = s.x; positions[off + 1] = s.y; positions[off + 2] = s.z;
      positions[off + 3] = t.x; positions[off + 4] = t.y; positions[off + 5] = t.z;

      const c = new THREE.Color("#1C8585");
      colors[off] = c.r * intensity;
      colors[off + 1] = c.g * intensity;
      colors[off + 2] = c.b * intensity;
      colors[off + 3] = c.r * intensity;
      colors[off + 4] = c.g * intensity;
      colors[off + 5] = c.b * intensity;
      valid++;
    }

    const geo = new THREE.BufferGeometry();
    geo.setAttribute("position", new THREE.BufferAttribute(positions.slice(0, valid * 6), 3));
    geo.setAttribute("color", new THREE.BufferAttribute(colors.slice(0, valid * 6), 3));
    return geo;
  }, [nodes, edges, highlightedIds]);

  return (
    <lineSegments geometry={geometry}>
      <lineBasicMaterial
        vertexColors
        transparent
        opacity={1}
        blending={THREE.AdditiveBlending}
        depthWrite={false}
        toneMapped={false}
      />
    </lineSegments>
  );
}
