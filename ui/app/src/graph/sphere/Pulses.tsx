/**
 * Honest pulses & flares (V3d) — the sphere fires the REAL turn path. Subscribes to
 * the same `pulseBus` the 2D BrainGraph does (fed by the unchanged `eventToActions`
 * derivation), so both renderers show the identical truth; only the geometry differs.
 *
 * A `pulse` sends a glowing sprite along the node→node great-circle arc (~500 ms); a
 * `flash` expands a ring tangent to the sphere at the node (~600 ms). Every effect
 * rides a real PulseAction — no timer fabricates motion, unplaced ids are dropped, and
 * dark edges (never emitted) stay dark.
 *
 * Perf discipline (mirrors BrainGraph): the bus fires at token rate, so the subscribe
 * handler mutates refs ONLY — never React state. Meshes are a declarative pool
 * (rendered once, invisible at rest); one useFrame drives every active animation.
 * Traveling sprites are gated on the governor's particle budget (shed at lite3d);
 * cheap node-local flashes stay. R3F disposes the pooled JSX geometry/material on
 * unmount, so there is nothing to hand-dispose (StrictMode-safe).
 */
import { useEffect, useMemo, useRef } from "react";
import { useFrame } from "@react-three/fiber";
import * as THREE from "three";
import { useBrain } from "../../store";
import type { GraphData } from "../../types";
import { subscribePulses, type PulseClass } from "../pulseBus";
import { sphereAnchors, type Vec3 } from "./sphereGeometry";
import { arcPoints } from "./greatCircle";
import { cssColor, PULSE_VAR } from "./materials";
import { tierToRender } from "./tierGate";
import {
  samplePolyline,
  makeCoalescer,
  makePool,
  pulseProgress,
  flashEnvelope,
} from "./pulseAnim";

const R = 3; // sphere radius — must match Scene.tsx so pulses ride the visible arcs
const PULSE_POOL = 32;
const FLASH_POOL = 16;
const Z = new THREE.Vector3(0, 0, 1);

interface ActivePulse {
  slot: number;
  points: Vec3[];
  age: number;
}
interface ActiveFlash {
  slot: number;
  age: number;
}

function nowMs(): number {
  return typeof performance !== "undefined" ? performance.now() : 0;
}

function pulseColor(klass: PulseClass): THREE.Color {
  return cssColor(PULSE_VAR[klass] ?? PULSE_VAR.normal, "#6ea8fe");
}

/** Radial (outward) unit direction of a local anchor; +Z fallback at the origin. */
function radialDir(pos: Vec3): THREE.Vector3 {
  const v = new THREE.Vector3(pos[0], pos[1], pos[2]);
  return v.lengthSq() < 1e-9 ? Z.clone() : v.normalize();
}

export default function Pulses({ graph }: { graph: GraphData | null }) {
  const renderTier = useBrain((s) => s.renderTier);
  const plan = tierToRender(renderTier);

  // id → local anchor (same math Scene uses). Live via a ref so the once-only
  // subscribe handler always sees the current topology without re-subscribing.
  const anchors = useMemo(
    () => (graph ? sphereAnchors(graph.nodes, R) : new Map<string, Vec3>()),
    [graph],
  );
  const anchorsRef = useRef(anchors);
  anchorsRef.current = anchors;
  const particlesRef = useRef(plan.particles);
  particlesRef.current = plan.particles;

  // Pools + per-edge coalescer + active lists — created once, mutated in place.
  const pulsePool = useMemo(() => makePool(PULSE_POOL), []);
  const flashPool = useMemo(() => makePool(FLASH_POOL), []);
  const coalescer = useMemo(() => makeCoalescer(150), []);
  const activePulses = useRef<ActivePulse[]>([]);
  const activeFlashes = useRef<ActiveFlash[]>([]);
  const pulseMeshes = useRef<(THREE.Mesh | null)[]>([]);
  const flashMeshes = useRef<(THREE.Mesh | null)[]>([]);

  useEffect(() => {
    return subscribePulses((a) => {
      if (a.type === "flash") {
        const pos = anchorsRef.current.get(a.node);
        if (!pos) return; // unplaced node → honest drop
        const slot = flashPool.acquire();
        if (slot === null) return; // pool exhausted → drop (cosmetic)
        const mesh = flashMeshes.current[slot];
        if (!mesh) {
          flashPool.release(slot);
          return;
        }
        mesh.position.set(pos[0], pos[1], pos[2]);
        mesh.quaternion.setFromUnitVectors(Z, radialDir(pos));
        mesh.scale.setScalar(1);
        const m = mesh.material as THREE.MeshStandardMaterial;
        const c = pulseColor(a.klass);
        m.color.copy(c);
        m.emissive.copy(c);
        m.opacity = 1;
        mesh.visible = true;
        activeFlashes.current.push({ slot, age: 0 });
        return;
      }

      // Traveling sprite = a "particle": shed with the governor's particle budget.
      if (!particlesRef.current) return;
      if (!coalescer.allow(`${a.from}>${a.to}`, nowMs())) return; // ≤~6/s/edge
      const A = anchorsRef.current.get(a.from);
      const B = anchorsRef.current.get(a.to);
      if (!A || !B) return; // unplaced endpoint → honest drop
      const slot = pulsePool.acquire();
      if (slot === null) return;
      const mesh = pulseMeshes.current[slot];
      if (!mesh) {
        pulsePool.release(slot);
        return;
      }
      const points = arcPoints(A, B, { segments: 20, bulge: 0.12 });
      const start = samplePolyline(points, 0);
      mesh.position.set(start[0], start[1], start[2]);
      const m = mesh.material as THREE.MeshStandardMaterial;
      const c = pulseColor(a.klass);
      m.color.copy(c);
      m.emissive.copy(c);
      mesh.visible = true;
      activePulses.current.push({ slot, points, age: 0 });
    });
    // Pools/coalescer/lists are stable refs; subscribe exactly once.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useFrame((_, delta) => {
    const ps = activePulses.current;
    for (let k = ps.length - 1; k >= 0; k--) {
      const p = ps[k];
      p.age += delta;
      const { t, done } = pulseProgress(p.age);
      const mesh = pulseMeshes.current[p.slot];
      if (done || !mesh) {
        if (mesh) mesh.visible = false;
        pulsePool.release(p.slot);
        ps.splice(k, 1);
        continue;
      }
      const pos = samplePolyline(p.points, t);
      mesh.position.set(pos[0], pos[1], pos[2]);
    }

    const fs = activeFlashes.current;
    for (let k = fs.length - 1; k >= 0; k--) {
      const f = fs[k];
      f.age += delta;
      const { scale, opacity, done } = flashEnvelope(f.age);
      const mesh = flashMeshes.current[f.slot];
      if (done || !mesh) {
        if (mesh) mesh.visible = false;
        flashPool.release(f.slot);
        fs.splice(k, 1);
        continue;
      }
      mesh.scale.setScalar(scale);
      (mesh.material as THREE.MeshStandardMaterial).opacity = opacity;
    }
  });

  return (
    <>
      {Array.from({ length: PULSE_POOL }).map((_, i) => (
        <mesh
          key={`p${i}`}
          ref={(m) => {
            pulseMeshes.current[i] = m;
          }}
          visible={false}
        >
          <sphereGeometry args={[0.05, 12, 12]} />
          {/* Emissive >1 crosses the bloom threshold (0.55) so the sprite glows. */}
          <meshStandardMaterial
            color="#6ea8fe"
            emissive="#6ea8fe"
            emissiveIntensity={2}
            toneMapped={false}
          />
        </mesh>
      ))}
      {Array.from({ length: FLASH_POOL }).map((_, i) => (
        <mesh
          key={`f${i}`}
          ref={(m) => {
            flashMeshes.current[i] = m;
          }}
          visible={false}
        >
          {/* Ring hugs the sphere surface at the node; scale/opacity animated. */}
          <ringGeometry args={[0.16, 0.24, 28]} />
          <meshStandardMaterial
            color="#6ea8fe"
            emissive="#6ea8fe"
            emissiveIntensity={2.2}
            transparent
            opacity={1}
            side={THREE.DoubleSide}
            depthWrite={false}
            toneMapped={false}
          />
        </mesh>
      ))}
    </>
  );
}
