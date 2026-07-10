/**
 * The central state gauge (V3e) — the 3D analogue of BrainGraph's `drawGauge`. A
 * ring around `baby_core` reflects the live `pipeline` state, and two of those
 * states ride REAL loudness: the listening ripple scales with mic RMS, the speaking
 * shimmer with TTS RMS (both honest signals off `micLevel()`/`ttsLevel()`). Idle
 * breathe is the sole ambient motion. Reduced-motion / performance mode collapse to
 * a static ring (mirrors the 2D `rm` path).
 *
 * Mounts as a Scene-level sibling (world space, NOT inside the rotating group) so
 * the flat ring keeps facing the camera at the origin. Cheap enough to keep at every
 * tier; the amplitude signals are read transiently in `useFrame` (never subscribed).
 */
import { useMemo, useRef } from "react";
import { useFrame } from "@react-three/fiber";
import * as THREE from "three";
import { useBrain } from "../../store";
import { cssColor, STATE_VAR } from "./materials";
import { micLevel, ttsLevel } from "../amplitude";

const R0 = 0.52; // ring radius (core sphere is r=0.34)
const TUBE = 0.028;

export default function CoreGauge() {
  const pipeline = useBrain((s) => s.pipeline);
  const performanceMode = useBrain((s) => s.performanceMode);
  const pipelineRef = useRef(pipeline);
  pipelineRef.current = pipeline;

  const reduced = useMemo(
    () =>
      typeof window !== "undefined" &&
      !!window.matchMedia &&
      window.matchMedia("(prefers-reduced-motion: reduce)").matches,
    [],
  );

  const ringRef = useRef<THREE.Mesh>(null);
  const ringMat = useRef<THREE.MeshStandardMaterial>(null);
  const orbitRef = useRef<THREE.Group>(null);

  useFrame((state) => {
    const t = state.clock.elapsedTime;
    const p = pipelineRef.current;
    const rm = reduced || performanceMode;
    const ring = ringRef.current;
    const mat = ringMat.current;
    if (!ring || !mat) return;

    // Colour always tracks the live state (idle grey → listening green → …).
    const col = cssColor(STATE_VAR[p] ?? STATE_VAR.idle, "#5a6273");
    mat.color.copy(col);
    mat.emissive.copy(col);

    let scale = 1;
    let opacity = 0.55;
    let emissive = 0.7;
    let spin = 0;
    let showOrbit = false;

    if (rm) {
      opacity = 0.8;
      emissive = 0.8;
    } else if (p === "listening") {
      scale = 1.12 + 0.7 * micLevel(); // ripple ∝ real mic loudness
      opacity = 0.85;
      emissive = 1.4;
    } else if (p === "thinking") {
      opacity = 0.7;
      emissive = 1.0;
      showOrbit = true;
    } else if (p === "speaking") {
      const s = ttsLevel(); // shimmer ∝ real TTS loudness
      scale = 1 + 0.08 * s;
      opacity = 0.4 + 0.5 * s;
      emissive = 0.8 + 2.0 * s;
    } else if (p === "executing") {
      spin = t * 1.6; // sweeping current
      opacity = 0.8;
      emissive = 1.2;
    } else {
      scale = 1 + 0.05 * Math.sin(t * 1.3); // idle breathe (ambient, allowed)
      opacity = 0.5;
      emissive = 0.6;
    }

    ring.scale.setScalar(scale);
    ring.rotation.z = spin;
    mat.opacity = opacity;
    mat.emissiveIntensity = emissive;

    const orbit = orbitRef.current;
    if (orbit) {
      orbit.visible = showOrbit;
      if (showOrbit) orbit.rotation.z = t * 1.2;
    }
  });

  return (
    <group>
      <mesh ref={ringRef}>
        <torusGeometry args={[R0, TUBE, 12, 48]} />
        <meshStandardMaterial
          ref={ringMat}
          color="#5a6273"
          emissive="#5a6273"
          emissiveIntensity={0.7}
          transparent
          opacity={0.55}
          toneMapped={false}
        />
      </mesh>
      {/* thinking-state orbiters (shown only while thinking) */}
      <group ref={orbitRef} visible={false}>
        {[0, 1, 2].map((i) => {
          const a = (i * 2 * Math.PI) / 3;
          return (
            <mesh key={i} position={[Math.cos(a) * R0, Math.sin(a) * R0, 0]}>
              <sphereGeometry args={[0.045, 12, 12]} />
              <meshStandardMaterial
                color="#6ea8fe"
                emissive="#6ea8fe"
                emissiveIntensity={1.6}
                toneMapped={false}
              />
            </mesh>
          );
        })}
      </group>
    </group>
  );
}
