// Adapted from DeusData/codebase-memory-mcp graph-ui (MIT).
import { Html } from "@react-three/drei";
import type { GraphNode } from "../types";
import { colorForNodeType } from "../graphState";

interface Props {
  node: GraphNode;
}

export function NodeTooltip({ node }: Props) {
  return (
    <Html
      position={[node.x, node.y + node.size * 0.9, node.z]}
      center
      style={{ pointerEvents: "none" }}
      zIndexRange={[100, 0]}
    >
      <div className="tooltip">
        <div className="tooltip-title">
          <span className="tooltip-dot" style={{ backgroundColor: colorForNodeType(node.subtype) }} />
          <span className="tooltip-name">{node.name}</span>
          <span className="tooltip-type">{node.subtype}</span>
        </div>
        {node.qualified_name && <p className="tooltip-path">{node.qualified_name}</p>}
        <p className="tooltip-meta">
          cluster {node.cluster} · degree {node.deg}
        </p>
        <p className="tooltip-hint">click for details</p>
      </div>
    </Html>
  );
}
