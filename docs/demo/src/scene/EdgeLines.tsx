// Adapted from DeusData/codebase-memory-mcp graph-ui (MIT).
import { useEffect, useMemo } from "react";
import * as THREE from "three";
import type { GraphNode, GraphEdge } from "../types";
import { colorForEdgeType, edgeIsVisible } from "../graphState";
import { edgeIntensityScale } from "./density";

interface Props {
  nodes: GraphNode[];
  edges: GraphEdge[];
  visibleIds: ReadonlySet<number>;
  focusedIds: ReadonlySet<number> | null;
  enabledTypes: ReadonlySet<string>;
  brightness: number;
}

export function EdgeLines({ nodes, edges, visibleIds, focusedIds, enabledTypes, brightness }: Props) {
  const built = useMemo(() => {
    const nodeById = new Map(nodes.map((node) => [node.id, node]));
    const positions = new Float32Array(edges.length * 6);
    const baseColors = new Float32Array(edges.length * 6);
    const colors = new Float32Array(edges.length * 6);
    const geometry = new THREE.BufferGeometry();
    const validEdges: GraphEdge[] = [];
    let valid = 0;

    for (const edge of edges) {
      const source = nodeById.get(edge.source);
      const target = nodeById.get(edge.target);
      if (!source || !target) continue;
      const offset = valid * 6;
      positions[offset] = source.x;
      positions[offset + 1] = source.y;
      positions[offset + 2] = source.z;
      positions[offset + 3] = target.x;
      positions[offset + 4] = target.y;
      positions[offset + 5] = target.z;
      const [r, g, b] = colorForEdgeType(edge.type);
      const clusterIntensity = source.cluster === target.cluster ? 0.22 : 0.07;
      baseColors[offset] = r * clusterIntensity;
      baseColors[offset + 1] = g * clusterIntensity;
      baseColors[offset + 2] = b * clusterIntensity;
      baseColors[offset + 3] = r * clusterIntensity;
      baseColors[offset + 4] = g * clusterIntensity;
      baseColors[offset + 5] = b * clusterIntensity;
      validEdges.push(edge);
      valid++;
    }

    geometry.setAttribute("position", new THREE.BufferAttribute(positions, 3));
    const colorAttribute = new THREE.BufferAttribute(colors, 3);
    colorAttribute.setUsage(THREE.DynamicDrawUsage);
    geometry.setAttribute("color", colorAttribute);
    geometry.setDrawRange(0, valid * 2);
    geometry.computeBoundingSphere();
    return { geometry, baseColors, colorAttribute, valid, validEdges };
  }, [nodes, edges]);

  useEffect(() => {
    const colors = built.colorAttribute.array as Float32Array;
    const density = edgeIntensityScale(built.valid) * brightness;
    const hasFocus = focusedIds !== null && focusedIds.size > 0;
    for (let i = 0; i < built.valid; i++) {
      const edge = built.validEdges[i];
      const visible = edgeIsVisible(edge, visibleIds, enabledTypes);
      const focused = hasFocus && focusedIds!.has(edge.source) && focusedIds!.has(edge.target);
      const scale = !visible ? 0 : hasFocus ? (focused ? brightness * 2.2 : density * 0.04) : density;
      const offset = i * 6;
      for (let channel = 0; channel < 6; channel++) colors[offset + channel] = built.baseColors[offset + channel] * scale;
    }
    built.colorAttribute.needsUpdate = true;
  }, [built, brightness, enabledTypes, focusedIds, visibleIds]);

  useEffect(() => () => built.geometry.dispose(), [built]);

  return (
    <lineSegments geometry={built.geometry}>
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
