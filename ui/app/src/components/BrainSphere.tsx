import { Canvas } from "@react-three/fiber";
import { Suspense, useEffect } from "react";
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

  return (
    <div className="graph-wrap">
      <Canvas
        camera={{ position: [0, 0, 7], fov: 50 }}
        dpr={[1, 1.5]}
        gl={{ powerPreference: "high-performance", antialias: plan.bloom }}
      >
        <Suspense fallback={null}>
          <Scene />
        </Suspense>
      </Canvas>
    </div>
  );
}
