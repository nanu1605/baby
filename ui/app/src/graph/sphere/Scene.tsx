/**
 * The R3F scene (V3b): honest topology on the sphere — nodes at deterministic
 * spherical anchors, dark great-circle edges (zero-signal stays dark), damped
 * OrbitControls, and ambient idle axis-rotation (the sole non-signal motion, #118).
 * Reuses the same store topology + tokens.css palette as the 2D graph.
 */
import { useEffect, useMemo, useRef } from "react";
import { useFrame, useThree } from "@react-three/fiber";
import { OrbitControls } from "three/examples/jsm/controls/OrbitControls.js";
import * as THREE from "three";
import { useBrain } from "../../store";
import type { GraphData } from "../../types";
import { sphereAnchors, type Vec3 } from "./sphereGeometry";
import { arcPoints } from "./greatCircle";
import { cssColor, nodeColor } from "./materials";
import { tierToRender } from "./tierGate";
import Effects from "./Effects";

const R = 3;
const TYPE_RADIUS: Record<string, number> = {
  core: 0.34,
  brain: 0.26,
  router: 0.24,
  safety: 0.24,
  memory: 0.2,
  voice: 0.18,
  tool: 0.14,
  infra: 0.16,
};

function Controls() {
  const { camera, gl } = useThree();
  const ref = useRef<OrbitControls | null>(null);
  useEffect(() => {
    const c = new OrbitControls(camera, gl.domElement);
    c.enablePan = false;
    c.enableDamping = true;
    c.dampingFactor = 0.08;
    c.rotateSpeed = 0.6;
    c.minDistance = 4;
    c.maxDistance = 14;
    ref.current = c;
    return () => c.dispose();
  }, [camera, gl]);
  useFrame(() => ref.current?.update());
  return null;
}

interface Placed {
  node: GraphData["nodes"][number];
  pos: Vec3;
}

function Edge({ pts, color }: { pts: Vec3[]; color: THREE.Color }) {
  const obj = useMemo(() => {
    const g = new THREE.BufferGeometry();
    const arr = new Float32Array(pts.length * 3);
    pts.forEach((p, i) => {
      arr[i * 3] = p[0];
      arr[i * 3 + 1] = p[1];
      arr[i * 3 + 2] = p[2];
    });
    g.setAttribute("position", new THREE.BufferAttribute(arr, 3));
    const m = new THREE.LineBasicMaterial({ color, transparent: true, opacity: 0.28 });
    return new THREE.Line(g, m);
  }, [pts, color]);
  useEffect(
    () => () => {
      obj.geometry.dispose();
      (obj.material as THREE.Material).dispose();
    },
    [obj],
  );
  return <primitive object={obj} />;
}

function Particles({ count = 320 }: { count?: number }) {
  const obj = useMemo(() => {
    const g = new THREE.BufferGeometry();
    const arr = new Float32Array(count * 3);
    const golden = Math.PI * (3 - Math.sqrt(5));
    for (let i = 0; i < count; i++) {
      const t = (i + 0.5) / count;
      const z = 1 - 2 * t;
      const r = Math.sqrt(Math.max(0, 1 - z * z));
      const az = i * golden;
      const rad = 4.1 + (i % 5) * 0.16; // thin shell of resting-state dust outside the sphere
      arr[i * 3] = rad * r * Math.cos(az);
      arr[i * 3 + 1] = rad * r * Math.sin(az);
      arr[i * 3 + 2] = rad * z;
    }
    g.setAttribute("position", new THREE.BufferAttribute(arr, 3));
    const m = new THREE.PointsMaterial({
      color: cssColor("--node-brain", "#6ea8fe"),
      size: 0.03,
      transparent: true,
      opacity: 0.45,
      depthWrite: false,
    });
    return new THREE.Points(g, m);
  }, [count]);
  useEffect(
    () => () => {
      obj.geometry.dispose();
      (obj.material as THREE.Material).dispose();
    },
    [obj],
  );
  return <primitive object={obj} />;
}

export default function Scene() {
  const graph = useBrain((s) => s.graph);
  const performanceMode = useBrain((s) => s.performanceMode);
  const renderTier = useBrain((s) => s.renderTier);
  const plan = tierToRender(renderTier);
  const groupRef = useRef<THREE.Group>(null);

  const reduced = useMemo(
    () =>
      typeof window !== "undefined" &&
      !!window.matchMedia &&
      window.matchMedia("(prefers-reduced-motion: reduce)").matches,
    [],
  );

  const { placed, edges } = useMemo(() => {
    if (!graph) return { placed: [] as Placed[], edges: [] as Vec3[][] };
    const anchors = sphereAnchors(graph.nodes, R);
    const placedNodes: Placed[] = graph.nodes
      .map((n) => ({ node: n, pos: anchors.get(n.id) }))
      .filter((p): p is Placed => !!p.pos);
    const edgeArcs = graph.edges
      .map((e) => {
        const a = anchors.get(e.source);
        const b = anchors.get(e.target);
        return a && b ? arcPoints(a, b, { segments: 20, bulge: 0.12 }) : null;
      })
      .filter((x): x is Vec3[] => !!x);
    return { placed: placedNodes, edges: edgeArcs };
  }, [graph]);

  // Ambient idle axis-rotation — the ONLY non-signal motion (#118); quiet under the
  // ⚡ performance opt-in or prefers-reduced-motion.
  useFrame((_, delta) => {
    if (groupRef.current && !performanceMode && !reduced) {
      groupRef.current.rotation.y += delta * 0.06;
    }
  });

  const edgeColor = cssColor("--edge", "#2a2f3a");

  return (
    <>
      <color attach="background" args={["#0b0d12"]} />
      <ambientLight intensity={0.55} />
      <pointLight position={[6, 6, 8]} intensity={1.1} />
      <Controls />
      <group ref={groupRef}>
        {placed.map(({ node, pos }) => (
          <mesh
            key={node.id}
            position={pos}
            onClick={(e) => {
              e.stopPropagation();
              useBrain.getState().selectNode(node.id);
            }}
          >
            <sphereGeometry args={[TYPE_RADIUS[node.type] ?? 0.16, 24, 24]} />
            <meshStandardMaterial
              color={nodeColor(node.type)}
              emissive={nodeColor(node.type)}
              emissiveIntensity={0.22}
              roughness={0.5}
              metalness={0.1}
            />
          </mesh>
        ))}
        {edges.map((pts, i) => (
          <Edge key={i} pts={pts} color={edgeColor} />
        ))}
      </group>
      {plan.particles && <Particles />}
      {plan.bloom && <Effects />}
    </>
  );
}
