// Adapted from DeusData/codebase-memory-mcp graph-ui (MIT).
import { useEffect, useMemo, useRef } from "react";
import * as THREE from "three";
import type { GraphNode } from "../types";
import { nodeGlowBoost, nodeBoostScale } from "./density";

interface Props {
  nodes: GraphNode[];
  visibleIds: ReadonlySet<number>;
  focusedIds: ReadonlySet<number> | null;
  selectedId: number | null;
  nodeGlow: number;
  onHover: (node: GraphNode | null) => void;
  onClick: (node: GraphNode) => void;
}

export function NodeCloud({ nodes, visibleIds, focusedIds, selectedId, nodeGlow, onHover, onClick }: Props) {
  const meshRef = useRef<THREE.InstancedMesh>(null);
  const tempObj = useMemo(() => new THREE.Object3D(), []);
  const tempColor = useMemo(() => new THREE.Color(), []);
  const boost = nodeBoostScale(nodes.length) * nodeGlow;
  const detail: [number, number, number] = nodes.length <= 8000 ? [1, 20, 14] : [1, 10, 7];

  // Allocate ONE color buffer per dataset and mutate it in place on highlight
  // changes, instead of reallocating a Float32Array + GPU buffer each hover.
  const colorAttr = useMemo(
    () => new THREE.InstancedBufferAttribute(new Float32Array(nodes.length * 3), 3),
    [nodes],
  );

  const nodeById = useMemo(() => new Map(nodes.map((n) => [n.id, n])), [nodes]);
  const idToIndex = useMemo(() => new Map(nodes.map((n, index) => [n.id, index])), [nodes]);

  // Static instance matrices (positions/scales never change with highlight).
  // Scale is updated in place only for the newly/prev selected node.
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

  // Recolor in place on every highlight / visibility / selection change; no
  // buffer realloc. Hidden nodes → near-zero color. Selected node gets an
  // extra brightness lift so it stays distinct from the hovered neighborhood.
  useEffect(() => {
    const hasFocus = focusedIds && focusedIds.size > 0;
    const arr = colorAttr.array as Float32Array;
    for (let i = 0; i < nodes.length; i++) {
      const node = nodes[i];
      if (!visibleIds.has(node.id)) {
        arr[i * 3] = arr[i * 3 + 1] = arr[i * 3 + 2] = 0;
        continue;
      }
      tempColor.set(node.color);
      if (hasFocus && !focusedIds!.has(node.id)) {
        tempColor.multiplyScalar(0.08);
      } else {
        tempColor.multiplyScalar(1 + (nodeGlowBoost(tempColor.r, tempColor.g, tempColor.b) - 1) * boost);
        if (node.id === selectedId) tempColor.multiplyScalar(1.6);
      }
      arr[i * 3] = tempColor.r;
      arr[i * 3 + 1] = tempColor.g;
      arr[i * 3 + 2] = tempColor.b;
    }
    colorAttr.needsUpdate = true;
  }, [nodes, visibleIds, focusedIds, selectedId, colorAttr, boost, tempColor]);

  // Selection scale emphasis: bump only the newly selected node and restore the
  // previously emphasized one, in place. Full rebuild on dataset change.
  const prevSelected = useRef<number | null>(null);
  useEffect(() => {
    const mesh = meshRef.current;
    if (!mesh) return;
    const restore = prevSelected.current;
    const apply = (id: number | null, scale: number) => {
      if (id === null) return;
      const idx = idToIndex.get(id);
      if (idx === undefined) return;
      const node = nodeById.get(id);
      if (!node) return;
      tempObj.position.set(node.x, node.y, node.z);
      tempObj.scale.setScalar(node.size * 0.55 * scale);
      tempObj.updateMatrix();
      mesh.setMatrixAt(idx, tempObj.matrix);
    };
    if (restore !== selectedId) {
      apply(restore, 1);
      apply(selectedId, 1.7);
      mesh.instanceMatrix.needsUpdate = true;
      prevSelected.current = selectedId;
    }
  }, [selectedId, nodeById, idToIndex, tempObj]);

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
        if (event.instanceId === undefined) return;
        const node = nodes[event.instanceId];
        if (!visibleIds.has(node.id)) return;
        event.stopPropagation();
        cancelPendingClear();
        onHover(node);
      }}
      onPointerOut={() => {
        cancelPendingClear();
        clearTimer.current = requestAnimationFrame(() => {
          clearTimer.current = null;
          onHover(null);
        });
      }}
      onClick={(event) => {
        if (event.instanceId === undefined) return;
        const node = nodes[event.instanceId];
        if (!visibleIds.has(node.id)) return;
        event.stopPropagation();
        onClick(node);
      }}
    >
      <sphereGeometry args={detail} />
      <meshBasicMaterial vertexColors toneMapped={false} />
      <primitive object={colorAttr} attach="geometry-attributes-color" />
    </instancedMesh>
  );
}
