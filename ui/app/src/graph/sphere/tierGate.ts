/**
 * Tier → render plan (V3a). The single mapping from the governor's quality tier to
 * what the 3D layer actually draws, so the governor (V2) is the sole on/off seam:
 *   full3d → sphere + bloom + particles
 *   lite3d → sphere, no bloom / no particles (shed on demote, same <Canvas>)
 *   2d     → no sphere; render the 2D BrainGraph floor instead (Canvas unmounts)
 *
 * Pure — vitest-tested; no three/React import so it stays in the entry bundle while
 * the actual sphere is lazy-loaded.
 */
import type { Tier } from "../governor/tierMachine";

export interface RenderPlan {
  /** Render the 3D sphere at all. */
  sphere: boolean;
  /** EffectComposer bloom (the #1 VRAM item — first to shed). */
  bloom: boolean;
  /** Ambient particle field / resting shimmer dust. */
  particles: boolean;
  /** Fall back to the 2D BrainGraph (the always-available floor). */
  floor2d: boolean;
}

export function tierToRender(tier: Tier): RenderPlan {
  switch (tier) {
    case "full3d":
      return { sphere: true, bloom: true, particles: true, floor2d: false };
    case "lite3d":
      return { sphere: true, bloom: false, particles: false, floor2d: false };
    case "2d":
    default:
      return { sphere: false, bloom: false, particles: false, floor2d: true };
  }
}
