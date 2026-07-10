/**
 * useGovernor — the live frame governor (V2). One rAF loop that samples real frame
 * time, ORs it with the VRAM watchdog, and steps the hysteretic tier machine, then
 * publishes the current tier to the store. Pure logic lives in fixedTimestep /
 * tierMachine / vramWatchdog (unit-tested); this hook only wires the clock + store.
 *
 * It does not draw — it protects the 60 fps contract by deciding which tier the 3D
 * layer (V3) may render at, and yields the GPU to the local model via the watchdog.
 * Cheap at idle (timing + a reducer, no repaint).
 */
import { useEffect } from "react";
import { useBrain } from "../../store";
import {
  DEFAULT_TIER_CONFIG,
  initialTierState,
  rankOf,
  TIER_ORDER,
  stepTier,
  type Tier,
  type TierConfig,
} from "./tierMachine";
import { modelCeiling } from "./vramWatchdog";

/** Lower of two tiers (used to fold the user's performance opt-in into the ceiling). */
function lower(a: Tier, b: Tier): Tier {
  return TIER_ORDER[Math.min(rankOf(a), rankOf(b))];
}

export function useGovernor(): void {
  useEffect(() => {
    let raf = 0;
    let stopped = false;
    let last = performance.now();
    let state = initialTierState();

    const tick = (now: number) => {
      if (stopped) return;
      raf = requestAnimationFrame(tick);

      const dtMs = now - last;
      last = now;
      if (!(dtMs > 0)) return;

      const b = useBrain.getState();
      const target = b.targetFps > 0 ? b.targetFps : 60;
      const budgetMs = 1000 / target;

      // An implausible stall (backgrounded tab / breakpoint) is ignored for BOTH
      // pressure AND accumulation: a single multi-second resume frame must neither
      // demote (a spurious spike) nor — on the calm path — promote a whole tier in
      // one frame (that would defeat "recover slowly"). The cutoff scales with the
      // budget so a low target_fps still leaves a working frame-pressure window.
      const stallCutoffMs = Math.max(100, budgetMs * 3);
      const plausible = dtMs < stallCutoffMs;
      const stepDt = plausible ? dtMs : 0; // a stall advances neither stress nor calm
      const framePressed = plausible && dtMs > budgetMs * 1.5;

      // The GPU watchdog is a CEILING, not ladder pressure: a resident local model
      // sheds bloom (lite3d) but keeps the sphere; everything else stays full3d.
      // Real fps contention (incl. VRAM paging) walks the ladder via frame
      // pressure; a lost WebGL context floors to 2D in BrainSphere.
      const ceiling = lower(
        lower(b.renderCeiling, b.performanceMode ? "lite3d" : "full3d"),
        modelCeiling(b.localModelLoaded),
      );
      const cfg: TierConfig = { ...DEFAULT_TIER_CONFIG, ceiling };

      state = stepTier(state, { pressured: framePressed, dtMs: stepDt, cfg });
      if (state.tier !== b.renderTier) b.setRenderTier(state.tier);
    };

    raf = requestAnimationFrame(tick);
    return () => {
      stopped = true;
      cancelAnimationFrame(raf);
    };
  }, []);
}
