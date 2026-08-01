// Adapted from DeusData/codebase-memory-mcp graph-ui (MIT).
import { useEffect, useMemo, useRef } from "react";
import * as THREE from "three";
import type { GraphNode } from "../types";
import { nodeGlowBoost } from "./density";

interface Props {
  nodes: GraphNode[];
  highlightedIds: Set<number> | null;
  onHover: (node: GraphNode | null) => void;
  onClick: (node: GraphNode) => void;
}

export function NodeCloud({ nodes, highlightedIds, onHover, onClick }: Props) {
  const meshRef = useRef<THREE.InstancedMesh>(null);
  const tempObj = useMemo(() => new THREE.Object3D(), []);
  const tempColor = useMemo(() => new THREE.Color(), []);
  const detail: [number, number, number] = nodes.length <= 8000 ? [1, 20, 14] : [1, 10, 7];

  const colors = useMemo(() => {
    const arr = new Float32Array(nodes.length * 3);
    const hasHighlight = highlightedIds && highlightedIds.size > 0;
    for (let i = 0; i < nodes.length; i++) {
      tempColor.set(nodes[i].color);
      if (hasHighlight && !highlightedIds.has(nodes[i].id)) {
        tempColor.multiplyScalar(0.08);
      } else {
        tempColor.multiplyScalar(nodeGlowBoost(tempColor.r, tempColor.g, tempColor.b));
      }
      arr[i * 3] = tempColor.r;
      arr[i * 3 + 1] = tempColor.g;
      arr[i * 3 + 2] = tempColor.b;
    }
    return arr;
  }, [nodes, highlightedIds, tempColor]);

  useEffect(() => {
    const mesh = meshRef.current;
    if (!mesh) return;
    const hasHighlight = highlightedIds && highlightedIds.size > 0;
    for (let i = 0; i < nodes.length; i++) {
      const node = nodes[i];
      tempObj.position.set(node.x, node.y, node.z);
      const selected = !hasHighlight || highlightedIds.has(node.id);
      const scale = node.size * (selected ? 0.55 : 0.25);
      tempObj.scale.setScalar(scale);
      tempObj.updateMatrix();
      mesh.setMatrixAt(i, tempObj.matrix);
    }
    mesh.instanceMatrix.needsUpdate = true;
    mesh.computeBoundingSphere();
  }, [nodes, highlightedIds, tempObj]);

  return (
    <instancedMesh
      key={nodes.length}
      ref={meshRef}
      args={[undefined, undefined, nodes.length]}
      frustumCulled={false}
      onPointerOver={(event) => {
        event.stopPropagation();
        if (event.instanceId !== undefined) onHover(nodes[event.instanceId]);
      }}
      onPointerOut={() => onHover(null)}
      onClick={(event) => {
        event.stopPropagation();
        if (event.instanceId !== undefined) onClick(nodes[event.instanceId]);
      }}
    >
      <sphereGeometry args={detail} />
      <meshBasicMaterial vertexColors toneMapped={false} />
      <instancedBufferAttribute attach="geometry-attributes-color" args={[colors, 3]} />
    </instancedMesh>
  );
}
