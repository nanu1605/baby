/**
 * VRAM watchdog (V2). The v4 law (#118): the 3D brain renders for free while the
 * GPU is idle (cloud-primary), but the GPU still yields to the LLM in the collision
 * window — when the local 9B loads/generates, VRAM jumps ~6.6 GB and 3D must demote.
 *
 * This is the pressure source the tier machine consumes for that collision. Pure:
 * it reads the VRAM signal pushed on /ws/state (used/total GB) and answers one
 * question — is the GPU too tight for 3D right now? Fail-open: no signal → no
 * pressure (a machine with no NVML still gets the full experience).
 */

export interface VramSignal {
  usedGb: number;
  totalGb: number;
}

export interface VramWatchdogConfig {
  /** Demote when used / total is at or above this fraction (the 9B resident). */
  highWaterFrac: number;
  /** Demote when free VRAM drops to or below this many GB (absolute headroom 3D wants). */
  minFreeGb: number;
}

/** 8 GB card: idle ~1 GB used → free ~7, no pressure; 9B loaded ~7.6 → fires. */
export const DEFAULT_VRAM_CONFIG: VramWatchdogConfig = {
  highWaterFrac: 0.8,
  minFreeGb: 1.5,
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
