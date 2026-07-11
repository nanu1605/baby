/**
 * GPU watchdog (V2, redesigned twice against the owner's real box in V3c). The v4
 * law (#118): the 3D brain renders for free while the GPU is idle (cloud-primary),
 * but the GPU yields to the LLM when the LLM is actually using it.
 *
 * Hard-won lesson: free-VRAM thresholds are a JUNK signal on Windows. Apps
 * overcommit and WDDM pages between them, so NVML "free" sits near zero on a busy
 * desktop even with nothing wrong — this box measured free 0.31 GB with ollama
 * completely empty while running full-bloom 3D without a hiccup. Two GB-pad designs
 * and one time-debounced design all stranded the sphere on the 2D floor because the
 * steady band brushes any threshold placed near it.
 *
 * So the watchdog keys on the ONE honest signal (the spec's own words: "the VRAM
 * watchdog auto-demotes 3D when the local 9B loads/generates"):
 *   local model resident      → ceiling lite3d (shed bloom/particles, keep the
 *                               sphere — leaves the LLM its headroom)
 *   not resident / unknown    → full3d (fail-open: no local model, no NVML, or the
 *                               game-mode offload all land here)
 * The FRAME governor independently demotes on real contention (paging shows up as
 * blown frame budgets), and a lost WebGL context floors to 2D (BrainSphere). The
 * raw vram_used/total numbers stay on /ws/state for observability only.
 */
import type { Tier } from "./tierMachine";

export interface VramSignal {
  usedGb: number;
  totalGb: number;
}

/** Quality ceiling from local-model residency. null/undefined = unknown → full. */
export function modelCeiling(localLoaded: boolean | null | undefined): Tier {
  return localLoaded === true ? "lite3d" : "full3d";
}
