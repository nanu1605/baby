/**
 * VRAM watchdog (V2). The v4 law (#118): the 3D brain renders for free while the
 * GPU is idle (cloud-primary), but the GPU still yields to the LLM in the collision
 * window. The honest question is "does 3D still have enough VRAM to run?", answered
 * from the free HEADROOM — not the fraction of a card that happens to be resident.
 *
 * The sphere is light (~a few hundred MB with bloom), so what matters is absolute
 * free GB, and that gate is card-size-correct: on an 8 GB card a resident 9B (~7.6
 * used, ~0.4 free) leaves no room → demote; on a 16 GB card the same 9B leaves ~9 GB
 * free → the centerpiece sphere STAYS. A fraction-of-total gate got this wrong (0.8
 * of 16 GB = demote at 12.8 used with 3 GB still free — plenty for the sphere), which
 * is why the primary gate is `minFreeGb` and the fraction is only a near-full safety.
 * The FRAME governor is the independent backstop for any real fps contention, so the
 * watchdog does not need to demote pre-emptively just because a model is resident.
 *
 * Pure: it reads the VRAM signal pushed on /ws/state (used/total GB) and answers the
 * one question. Fail-open: no signal → no pressure (no-NVML machine → full experience).
 */

export interface VramSignal {
  usedGb: number;
  totalGb: number;
}

export interface VramWatchdogConfig {
  /** Absolute free-VRAM headroom the sphere wants; the PRIMARY, card-size-correct gate. */
  minFreeGb: number;
  /** Near-full safety only (catches a bogus total / a very large card); NOT the main gate. */
  highWaterFrac: number;
}

/**
 * Free headroom is the real gate. 8 GB + 9B (~0.4 free) → demote; 16 GB + 9B (~9 free)
 * → sphere stays. highWaterFrac was 0.8 — an 8 GB heuristic that demoted a 16 GB card
 * with 3 GB free; now a near-full backstop only.
 */
export const DEFAULT_VRAM_CONFIG: VramWatchdogConfig = {
  minFreeGb: 1.5,
  highWaterFrac: 0.94,
};

export function vramPressured(
  sig: VramSignal | null | undefined,
  cfg: VramWatchdogConfig = DEFAULT_VRAM_CONFIG,
): boolean {
  if (!sig) return false; // no NVML / no signal yet → full experience
  const { usedGb, totalGb } = sig;
  if (!(totalGb > 0) || !(usedGb >= 0)) return false;
  const usedFrac = usedGb / totalGb;
  const freeGb = totalGb - usedGb;
  return usedFrac >= cfg.highWaterFrac || freeGb <= cfg.minFreeGb;
}
