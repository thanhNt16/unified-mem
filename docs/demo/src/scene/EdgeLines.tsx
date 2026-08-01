// Adapted from DeusData/codebase-memory-mcp graph-ui (MIT).
import { useMemo } from "react";
import * as THREE from "three";
import type { GraphNode, GraphEdge } from "../types";
import { edgeIntensityScale } from "./density";

interface Props {
  nodes: GraphNode[];
  edges: GraphEdge[];
}

export function EdgeLines({ nodes, edges }: Props) {
  const geometry = useMemo(() => {
    const densityScale = edgeIntensityScale(edges.length);
    const idToIdx = new Map<number, number>();
    for (let i = 0; i < nodes.length; i++) idToIdx.set(nodes[i].id, i);

    const positions = new Float32Array(edges.length * 6);
    const colors = new Float32Array(edges.length * 6);
    const edgeColor = new THREE.Color("#1C8585");
    let valid = 0;

    for (const edge of edges) {
      const si = idToIdx.get(edge.source);
      const ti = idToIdx.get(edge.target);
      if (si === undefined || ti === undefined) continue;

      const s = nodes[si];
      const t = nodes[ti];
      const intensity = (s.cluster === t.cluster ? 0.22 : 0.07) * densityScale;
      const off = valid * 6;
      positions[off] = s.x; positions[off + 1] = s.y; positions[off + 2] = s.z;
      positions[off + 3] = t.x; positions[off + 4] = t.y; positions[off + 5] = t.z;
      colors[off] = edgeColor.r * intensity;
      colors[off + 1] = edgeColor.g * intensity;
      colors[off + 2] = edgeColor.b * intensity;
      colors[off + 3] = edgeColor.r * intensity;
      colors[off + 4] = edgeColor.g * intensity;
      colors[off + 5] = edgeColor.b * intensity;
      valid++;
    }

    const geo = new THREE.BufferGeometry();
    geo.setAttribute("position", new THREE.BufferAttribute(positions.slice(0, valid * 6), 3));
    geo.setAttribute("color", new THREE.BufferAttribute(colors.slice(0, valid * 6), 3));
    return geo;
  }, [nodes, edges]);

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
