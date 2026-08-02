// Adapted from DeusData/codebase-memory-mcp graph-ui (MIT).
import { useMemo } from "react";
import { Html } from "@react-three/drei";
import type { GraphNode } from "../types";

const MAX_LABELS = 80;

interface Props {
  nodes: GraphNode[];
  focusedIds: ReadonlySet<number> | null;
  visibleIds: ReadonlySet<number>;
}

export function NodeLabels({ nodes, focusedIds, visibleIds }: Props) {
  const labels = useMemo(() => {
    const focused = focusedIds && focusedIds.size > 0;
    return nodes
      .filter((node) => visibleIds.has(node.id) && (!focused || focusedIds!.has(node.id)))
      .sort((a, b) => b.deg - a.deg || b.size - a.size)
      .slice(0, MAX_LABELS);
  }, [nodes, focusedIds, visibleIds]);

  return (
    <>
      {labels.map((node) => (
        <Html
          key={node.id}
          position={[node.x, node.y + node.size * 0.75, node.z]}
          center
          distanceFactor={750}
          style={{ pointerEvents: "none" }}
          zIndexRange={[2, 0]}
        >
          <span className="node-label">{node.name}</span>
        </Html>
      ))}
    </>
  );
}
