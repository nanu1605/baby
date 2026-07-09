// V0 shell spike — the shared 3D scene. Byte-identical across shells.
//
// A bloom-lit spinning sphere: ~42 emissive point nodes + ~50 glowing arcs, a
// deterministic idle rotation, and a fixed time-based camera path so both shells
// traverse an identical route during the 60 s measurement window. This is a
// throwaway stress scene, NOT the real V3 brain — it exists only to compare how
// Tauri (WebView2) vs Electron (Chromium) render postprocessing/bloom on the
// 8 GB card.

import { useMemo, useRef } from "react";
import { Canvas, useFrame, useThree } from "@react-three/fiber";
import { EffectComposer, Bloom } from "@react-three/postprocessing";
import * as THREE from "three";
import { NODES, ARCS } from "./nodes";
import type { SpikeHarness } from "./harness";

const CAMERA_RADIUS = 8;

/** Drives the fixed camera path + feeds every frame delta to the harness. */
function Driver({ harness }: { harness: SpikeHarness }) {
  const { camera } = useThree();
  useFrame((_state, delta) => {
    const t = performance.now() / 1000;
    // Deterministic orbit — same at any wall-clock time regardless of fps.
    camera.position.set(
      Math.sin(t * 0.18) * CAMERA_RADIUS,
      Math.sin(t * 0.06) * CAMERA_RADIUS * 0.35,
      Math.cos(t * 0.18) * CAMERA_RADIUS,
    );
    camera.lookAt(0, 0, 0);
    harness.frame(delta);
  });
  return null;
}

function Nodes() {
  const group = useRef<THREE.Group>(null);
  const geo = useMemo(() => new THREE.SphereGeometry(0.11, 16, 16), []);
  const mats = useMemo(
    () =>
      NODES.map((n) => {
        const col = new THREE.Color().setHSL(n.hue, 0.7, 0.55);
        return new THREE.MeshStandardMaterial({
          color: col,
          emissive: col,
          emissiveIntensity: 2.2,
          toneMapped: false, // let bloom pick up the bright emissive
        });
      }),
    [],
  );
  useFrame((_s, delta) => {
    if (group.current) group.current.rotation.y += delta * 0.15; // idle spin
  });
  return (
    <group ref={group}>
      {NODES.map((n, i) => (
        <mesh key={n.id} geometry={geo} material={mats[i]} position={[n.x, n.y, n.z]} />
      ))}
      <Arcs />
    </group>
  );
}

function Arcs() {
  const geometry = useMemo(() => {
    const pts: number[] = [];
    for (const arc of ARCS) {
      const a = NODES[arc.a];
      const b = NODES[arc.b];
      pts.push(a.x, a.y, a.z, b.x, b.y, b.z);
    }
    const g = new THREE.BufferGeometry();
    g.setAttribute("position", new THREE.Float32BufferAttribute(pts, 3));
    return g;
  }, []);
  const material = useMemo(
    () =>
      new THREE.LineBasicMaterial({
        color: new THREE.Color("#6ea8fe"),
        transparent: true,
        opacity: 0.6,
        toneMapped: false,
      }),
    [],
  );
  return <lineSegments geometry={geometry} material={material} />;
}

export function SpikeCanvas({ harness }: { harness: SpikeHarness }) {
  return (
    <Canvas
      gl={{ antialias: true, powerPreference: "high-performance" }}
      camera={{ position: [0, 0, CAMERA_RADIUS], fov: 55 }}
      dpr={[1, 2]}
    >
      <color attach="background" args={["#0b0d12"]} />
      <ambientLight intensity={0.35} />
      <pointLight position={[6, 6, 6]} intensity={1.2} />
      <Nodes />
      <Driver harness={harness} />
      <EffectComposer>
        <Bloom
          intensity={1.25}
          luminanceThreshold={0.2}
          luminanceSmoothing={0.9}
          mipmapBlur
        />
      </EffectComposer>
    </Canvas>
  );
}
