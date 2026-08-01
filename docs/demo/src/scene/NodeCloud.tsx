// Adapted from DeusData/codebase-memory-mcp graph-ui (MIT).
import { useEffect, useMemo, useRef } from "react";
import * as THREE from "three";
import type { GraphNode } from "../types";
import { nodeGlowBoost, nodeBoostScale } from "./density";

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
  const boost = nodeBoostScale(nodes.length);
  const detail: [number, number, number] = nodes.length <= 8000 ? [1, 20, 14] : [1, 10, 7];

  // Allocate ONE color buffer per dataset and mutate it in place on highlight
  // changes, instead of reallocating a Float32Array + GPU buffer each hover.
  const colorAttr = useMemo(
    () => new THREE.InstancedBufferAttribute(new Float32Array(nodes.length * 3), 3),
    [nodes],
  );

  // Static instance matrices (positions/scales never change with highlight).
  useEffect(() => {
    const mesh = meshRef.current;
    if (!mesh) return;
    for (let i = 0; i < nodes.length; i++) {
      const node = nodes[i];
      tempObj.position.set(node.x, node.y, node.z);
      tempObj.scale.setScalar(node.size * 0.55);
      tempObj.updateMatrix();
      mesh.setMatrixAt(i, tempObj.matrix);
    }
    mesh.instanceMatrix.needsUpdate = true;
    mesh.computeBoundingSphere();
  }, [nodes, tempObj]);

  // Recolor in place on every highlight change; no buffer realloc.
  useEffect(() => {
    const hasHighlight = highlightedIds && highlightedIds.size > 0;
    const arr = colorAttr.array as Float32Array;
    for (let i = 0; i < nodes.length; i++) {
      tempColor.set(nodes[i].color);
      if (hasHighlight && !highlightedIds.has(nodes[i].id)) {
        tempColor.multiplyScalar(0.08);
      } else {
        tempColor.multiplyScalar(1 + (nodeGlowBoost(tempColor.r, tempColor.g, tempColor.b) - 1) * boost);
      }
      arr[i * 3] = tempColor.r;
      arr[i * 3 + 1] = tempColor.g;
      arr[i * 3 + 2] = tempColor.b;
    }
    colorAttr.needsUpdate = true;
  }, [nodes, highlightedIds, colorAttr, boost, tempColor]);

  // One-frame hover-clear debounce: moving across adjacent instances fires
  // pointerout before the next pointerover lands. Cancel a pending clear if a
  // new hover arrives within the same frame.
  const clearTimer = useRef<number | null>(null);
  const cancelPendingClear = () => {
    if (clearTimer.current !== null) {
      cancelAnimationFrame(clearTimer.current);
      clearTimer.current = null;
    }
  };
  useEffect(() => cancelPendingClear, []);

  return (
    <instancedMesh
      key={nodes.length}
      ref={meshRef}
      args={[undefined, undefined, nodes.length]}
      frustumCulled={false}
      onPointerOver={(event) => {
        event.stopPropagation();
        cancelPendingClear();
        if (event.instanceId !== undefined) onHover(nodes[event.instanceId]);
      }}
      onPointerOut={() => {
        cancelPendingClear();
        clearTimer.current = requestAnimationFrame(() => {
          clearTimer.current = null;
          onHover(null);
        });
      }}
      onClick={(event) => {
        event.stopPropagation();
        if (event.instanceId !== undefined) onClick(nodes[event.instanceId]);
      }}
    >
      <sphereGeometry args={detail} />
      <meshBasicMaterial vertexColors toneMapped={false} />
      <primitive object={colorAttr} attach="geometry-attributes-color" />
    </instancedMesh>
  );
}
