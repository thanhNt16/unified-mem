// Adapted from DeusData/codebase-memory-mcp graph-ui (MIT).
import { useEffect, useRef, useState } from "react";
import { Canvas, useFrame, useThree } from "@react-three/fiber";
import { OrbitControls } from "@react-three/drei";
import { EffectComposer, Bloom } from "@react-three/postprocessing";
import * as THREE from "three";
import type { OrbitControls as OrbitControlsImpl } from "three-stdlib";
import type { CameraTarget, DisplaySettings } from "../graphState";
import type { GraphData, GraphNode } from "../types";
import { bloomIntensityScale } from "./density";
import { EdgeLines } from "./EdgeLines";
import { NodeCloud } from "./NodeCloud";
import { NodeTooltip } from "./NodeTooltip";
import { NodeLabels } from "./NodeLabels";

interface Props {
  data: GraphData;
  visibleIds: ReadonlySet<number>;
  focusedIds: ReadonlySet<number> | null;
  selectedId: number | null;
  hovered: GraphNode | null;
  cameraTarget: CameraTarget | null;
  cameraGeneration: number;
  display: DisplaySettings;
  edgeTypes: ReadonlySet<string>;
  showLabels: boolean;
  onHover: (node: GraphNode | null) => void;
  onNodeClick: (node: GraphNode) => void;
  onBackgroundClick: () => void;
}

const prefersReducedMotion = () =>
  typeof window !== "undefined" && window.matchMedia("(prefers-reduced-motion: reduce)").matches;

function CameraControls({ target, generation }: { target: CameraTarget | null; generation: number }) {
  const { camera } = useThree();
  const controls = useRef<OrbitControlsImpl>(null);
  const [autoRotate, setAutoRotate] = useState(!prefersReducedMotion());
  const idleTimer = useRef<number | null>(null);
  const move = useRef<{ start: number; from: THREE.Vector3; to: THREE.Vector3; lookFrom: THREE.Vector3; lookTo: THREE.Vector3 } | null>(null);

  const markInteraction = () => {
    setAutoRotate(false);
    if (idleTimer.current !== null) window.clearTimeout(idleTimer.current);
    if (!prefersReducedMotion()) idleTimer.current = window.setTimeout(() => setAutoRotate(true), 60000);
  };

  useEffect(() => {
    const orbit = controls.current;
    if (!target || !orbit) return;
    markInteraction();
    const to = new THREE.Vector3(...target.position);
    const lookTo = new THREE.Vector3(...target.lookAt);
    if (prefersReducedMotion()) {
      camera.position.copy(to);
      orbit.target.copy(lookTo);
      orbit.update();
    } else {
      move.current = { start: performance.now(), from: camera.position.clone(), to, lookFrom: orbit.target.clone(), lookTo };
    }
  // generation intentionally lets reset/refocus replay the same target.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [target, generation, camera]);

  useEffect(() => () => {
    if (idleTimer.current !== null) window.clearTimeout(idleTimer.current);
  }, []);

  useFrame(() => {
    const animation = move.current;
    const orbit = controls.current;
    if (!animation || !orbit) return;
    const t = Math.min(1, (performance.now() - animation.start) / 700);
    const eased = 1 - Math.pow(1 - t, 3);
    camera.position.lerpVectors(animation.from, animation.to, eased);
    orbit.target.lerpVectors(animation.lookFrom, animation.lookTo, eased);
    orbit.update();
    if (t === 1) move.current = null;
  });

  return (
    <OrbitControls
      ref={controls}
      enableDamping
      dampingFactor={0.08}
      rotateSpeed={0.5}
      zoomSpeed={1.5}
      minDistance={10}
      maxDistance={50000}
      autoRotate={autoRotate}
      autoRotateSpeed={0.25}
      onStart={markInteraction}
    />
  );
}

function SceneContent(props: Props) {
  return (
    <>
      <color attach="background" args={["#06090f"]} />
      <EdgeLines
        nodes={props.data.nodes}
        edges={props.data.edges}
        visibleIds={props.visibleIds}
        focusedIds={props.focusedIds}
        enabledTypes={props.edgeTypes}
        brightness={props.display.edgeBrightness}
      />
      <NodeCloud
        nodes={props.data.nodes}
        visibleIds={props.visibleIds}
        focusedIds={props.focusedIds}
        selectedId={props.selectedId}
        nodeGlow={props.display.nodeGlow}
        onHover={props.onHover}
        onClick={props.onNodeClick}
      />
      {props.showLabels && <NodeLabels nodes={props.data.nodes} focusedIds={props.focusedIds} visibleIds={props.visibleIds} />}
      {props.hovered && props.visibleIds.has(props.hovered.id) && <NodeTooltip node={props.hovered} />}
      <EffectComposer multisampling={0}>
        <Bloom
          luminanceThreshold={0.3}
          luminanceSmoothing={0.7}
          intensity={1.45 * bloomIntensityScale(props.data.nodes.length) * props.display.bloom}
          mipmapBlur
          radius={0.6}
        />
      </EffectComposer>
      <CameraControls target={props.cameraTarget} generation={props.cameraGeneration} />
    </>
  );
}

export function GraphScene(props: Props) {
  return (
    <div className="scene">
      <Canvas
        camera={{ position: [0, 0, 820], fov: 50, near: 0.1, far: 100000 }}
        dpr={[1, 1.5]}
        gl={{ antialias: false, alpha: false, powerPreference: "high-performance" }}
        onPointerMissed={props.onBackgroundClick}
      >
        <SceneContent {...props} />
      </Canvas>
    </div>
  );
}
