/**
 * VRAM watchdog (V2, graduated in V3c after the first real-GPU checkpoint). The v4
 * law (#118): the 3D brain renders for free while the GPU is idle (cloud-primary),
 * but the GPU yields to the LLM when VRAM is genuinely tight.
 *
 * Reality check that shaped this: the owner's 5060 Ti is the 8 GB variant, and the
 * local 9B is kept warm as the offline fallback — resident it leaves ~0.7 GB free.
 * A boolean watchdog (pressure → walk the whole ladder down) therefore parked the
 * centerpiece sphere on the 2D floor for the entire game-mode-off daily state.
 * That over-yields: the LITE sphere is light (no bloom composer — the HDR render
 * targets are the #1 VRAM item), and the FRAME governor independently measures real
 * contention and demotes on actual stutter.
 *
 * So the watchdog now answers with a quality CEILING, not a boolean:
 *   free >  shedFreeGb                  → full3d  (bloom and all)
 *   free <= shedFreeGb (or near-full)   → lite3d  (shed bloom/particles, keep the sphere)
 *   free <= floorFreeGb                 → 2d      (genuinely no room — the honest floor)
 * Recovery needs `hysteresisGb` of extra headroom so a 0.25 GB bucket wobbling on a
 * threshold never flaps the ceiling (demote-fast / recover-slow, like the tier
 * machine). Pure: previous ceiling in, next ceiling out — unit-tests without a clock.
 * Fail-open: no signal → full3d (a machine with no NVML gets the full experience).
 */
import { rankOf, type Tier } from "./tierMachine";

export interface VramSignal {
  usedGb: number;
  totalGb: number;
}

export interface VramWatchdogConfig {
  /** Free GB at or below which bloom/particles shed (ceiling lite3d). */
  shedFreeGb: number;
  /** Free GB at or below which even the lite sphere yields (ceiling 2d). */
  floorFreeGb: number;
  /** Extra free GB required to LIFT a ceiling again (anti-flap). */
  hysteresisGb: number;
  /** Near-full fraction safety — at least sheds, whatever the absolute numbers. */
  highWaterFrac: number;
}

/**
 * Tuned against the owner's live box (8 GB card, 9B warm → free hovers 0.4–1.05 GB):
 * the floor sits below that band (the lite sphere itself needs ~0.2 GB; under 0.35
 * free the frame governor would be firing anyway). The hysteresis pad must exceed
 * the promoted tier's OWN VRAM footprint (review-caught): NVML free is measured
 * while the tier is shed, so lifting re-allocates (bloom composer / whole canvas at
 * the floor) and a pad smaller than that allocation is an indefinite
 * lift→promote→allocate→shed loop. With the composer at multisampling 0 (~0.15 GB,
 * see Effects.tsx) and the canvas remount ~0.3 GB, 0.5 GB covers both with margin —
 * and, live-measured, still lets the owner's box recover off the floor after a
 * model-reload spike (it settles near free 1.05–1.1, and the lift point is 0.85;
 * a 0.75 pad left it stranded on 2d by two hundredths of a GB).
 */
export const DEFAULT_VRAM_CONFIG: VramWatchdogConfig = {
  shedFreeGb: 1.5,
  floorFreeGb: 0.35,
  hysteresisGb: 0.5,
  highWaterFrac: 0.94,
};

/** Ceiling for the given headroom; `pad` widens the thresholds (used for recovery). */
function levelFor(freeGb: number, totalGb: number, cfg: VramWatchdogConfig, pad: number): Tier {
  if (freeGb <= cfg.floorFreeGb + pad) return "2d";
  // The near-full fraction is expressed as a free-GB threshold so the hysteresis
  // pad applies to it uniformly (review-caught: a bare `usedFrac >=` test had zero
  // hysteresis and flapped the ceiling on >29 GB cards wobbling one bucket at 0.94).
  const shedAt = Math.max(cfg.shedFreeGb, (1 - cfg.highWaterFrac) * totalGb);
  if (freeGb <= shedAt + pad) return "lite3d";
  return "full3d";
}

/**
 * The VRAM-derived quality ceiling. Restricting (lower ceiling) applies immediately;
 * lifting requires `hysteresisGb` of extra headroom past the threshold.
 */
export function vramCeiling(
  sig: VramSignal | null | undefined,
  prev: Tier = "full3d",
  cfg: VramWatchdogConfig = DEFAULT_VRAM_CONFIG,
): Tier {
  if (!sig) return "full3d"; // no NVML / no signal yet → full experience
  const { usedGb, totalGb } = sig;
  if (!(totalGb > 0) || !(usedGb >= 0)) return "full3d";
  const freeGb = totalGb - usedGb;

  const drop = levelFor(freeGb, totalGb, cfg, 0);
  if (rankOf(drop) < rankOf(prev)) return drop; // tighter now — restrict at once

  const rise = levelFor(freeGb, totalGb, cfg, cfg.hysteresisGb);
  return rankOf(rise) > rankOf(prev) ? rise : prev; // lift only with real headroom
}
