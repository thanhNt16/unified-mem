// Adapted from DeusData/codebase-memory-mcp graph-ui (MIT).
import { useState } from "react";
import { Canvas } from "@react-three/fiber";
import { OrbitControls } from "@react-three/drei";
import { EffectComposer, Bloom } from "@react-three/postprocessing";
import type { GraphData, GraphNode } from "../types";
import { EdgeLines } from "./EdgeLines";
import { NodeCloud } from "./NodeCloud";

interface Props {
  data: GraphData;
  highlightedIds: Set<number> | null;
  onNodeClick: (node: GraphNode) => void;
}

export function GraphScene({ data, highlightedIds, onNodeClick }: Props) {
  const [hovered, setHovered] = useState<GraphNode | null>(null);

  return (
    <div className="scene">
      <Canvas
        camera={{ position: [0, 0, 820], fov: 50, near: 0.1, far: 100000 }}
        dpr={[1, 1.5]}
        gl={{ antialias: false, alpha: false, powerPreference: "high-performance" }}
      >
        <color attach="background" args={["#06090f"]} />
        <ambientLight intensity={0.5} />
        <pointLight position={[500, 500, 500]} intensity={0.6} />
        <EdgeLines nodes={data.nodes} edges={data.edges} highlightedIds={highlightedIds} />
        <NodeCloud
          nodes={data.nodes}
          highlightedIds={highlightedIds}
          onHover={setHovered}
          onClick={onNodeClick}
        />
        <EffectComposer multisampling={0}>
          <Bloom luminanceThreshold={0.3} luminanceSmoothing={0.7} intensity={1.45} mipmapBlur radius={0.6} />
        </EffectComposer>
        <OrbitControls
          enableDamping
          dampingFactor={0.08}
          rotateSpeed={0.5}
          zoomSpeed={1.5}
          minDistance={10}
          maxDistance={50000}
          autoRotate
          autoRotateSpeed={0.25}
        />
      </Canvas>
      {hovered && (
        <div className="tooltip">
          <strong>{hovered.name}</strong><br />
          {hovered.subtype} · degree {hovered.deg} · cluster {hovered.cluster}
        </div>
      )}
    </div>
  );
}
