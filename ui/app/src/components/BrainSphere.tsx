import { Canvas } from "@react-three/fiber";
import { Suspense, useEffect, useState } from "react";
import { useBrain } from "../store";
import { getGraph } from "../api/client";
import { tierToRender } from "../graph/sphere/tierGate";
import Scene from "../graph/sphere/Scene";

/**
 * BrainSphere — the 3D sibling of BrainGraph, lazy-loaded so 2D-only users never
 * fetch three. Mounts the <Canvas>, lifts topology into the store (so the
 * InspectorDrawer + search still resolve node ids while the 2D floor is unmounted),
 * and renders the honest scene. Bloom/particles land in V3c, gated by the tier.
 */
export default function BrainSphere() {
  const renderTier = useBrain((s) => s.renderTier);
  const plan = tierToRender(renderTier);
  const [glCanvas, setGlCanvas] = useState<HTMLCanvasElement | null>(null);

  useEffect(() => {
    let alive = true;
    getGraph()
      .then((g) => {
        if (alive) useBrain.getState().setGraph(g);
      })
      .catch(() => {});
    return () => {
      alive = false;
    };
  }, []);

  // Context-loss floor (V3f, pulled forward): a dead WebGL context falls back to
  // the 2D graph instead of a black stage; the store schedules one retry per loss.
  // MOUNT-SCOPED on purpose (review-caught): R3F v8 fires a deliberate
  // forceContextLoss ~500 ms AFTER every Canvas unmount as part of its own
  // teardown — an unremovable onCreated listener would catch that and turn every
  // legitimate tier-dip to 2d into a spurious 60 s "context lost" lockout. React
  // runs this effect's cleanup synchronously at unmount, well before that delayed
  // teardown event, so only losses on a LIVE canvas ever reach the store.
  useEffect(() => {
    if (!glCanvas) return;
    const onLost = (e: Event) => {
      e.preventDefault();
      useBrain.getState().setContextLost(true);
    };
    glCanvas.addEventListener("webglcontextlost", onLost);
    return () => glCanvas.removeEventListener("webglcontextlost", onLost);
  }, [glCanvas]);

  return (
    <div className="graph-wrap">
      <Canvas
        camera={{ position: [0, 0, 7], fov: 50 }}
        dpr={[1, 1.5]}
        gl={{ powerPreference: "high-performance", antialias: plan.bloom }}
        onCreated={({ gl }) => setGlCanvas(gl.domElement)}
      >
        <Suspense fallback={null}>
          <Scene />
        </Suspense>
      </Canvas>
    </div>
  );
}
