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
 * So the watchdog answers with a quality CEILING, not a boolean:
 *   free >  shedFreeGb                  → full3d  (bloom and all)
 *   free <= shedFreeGb (or near-full)   → lite3d  (shed bloom/particles, keep the sphere)
 *   free <= floorFreeGb SUSTAINED       → 2d      (genuinely no room — the honest floor)
 * Shed recovery uses a GB pad; the floor is time-debounced BOTH ways (see the
 * config docs — GB-only pads kept stranding the owner's box). Pure step function:
 * state in, state out — unit-tests without a clock. Fail-open: no signal → full3d
 * (a machine with no NVML gets the full experience).
 */
import type { Tier } from "./tierMachine";

export interface VramSignal {
  usedGb: number;
  totalGb: number;
}

export interface VramWatchdogConfig {
  /** Free GB at or below which bloom/particles shed (ceiling lite3d). */
  shedFreeGb: number;
  /** Extra free GB required to lift the SHED cap again (anti-flap; > bloom's ~0.15). */
  shedLiftPadGb: number;
  /** Free GB at or below which even the lite sphere yields (ceiling 2d). */
  floorFreeGb: number;
  /** Sustained ms below floorFreeGb before actually flooring (spike immunity). */
  floorDropMs: number;
  /** Extra free GB (over the floor) required to lift off 2d — the canvas re-alloc. */
  floorLiftPadGb: number;
  /** Sustained ms of that headroom before lifting off 2d. */
  floorLiftMs: number;
  /** Near-full fraction safety — at least sheds, whatever the absolute numbers. */
  highWaterFrac: number;
}

/**
 * Tuned against the owner's live box (8 GB card, 9B warm → free hovers 0.4–1.1 GB,
 * BRUSHING any threshold placed near it — two successive GB-only recovery pads left
 * the box stranded on the 2D floor by 0.01–0.02 GB). So the FLOOR transition is
 * time-debounced in both directions instead:
 *   - dropping to 2d needs free <= floorFreeGb SUSTAINED floorDropMs — a model
 *     reload spike (seconds) never floors at all; the frame governor independently
 *     covers any real stutter during it;
 *   - lifting off 2d needs only enough headroom that the canvas re-alloc (~0.3 GB)
 *     cannot re-breach the floor (floorFreeGb + floorLiftPadGb), sustained
 *     floorLiftMs — no big pad to collide with the steady band.
 * The SHED cap keeps a simple GB pad: it only gates the bloom composer, whose
 * multisampling-0 footprint (~0.15 GB, Effects.tsx) is far below the pad, and the
 * shed threshold sits well above the owner's steady band anyway.
 */
export const DEFAULT_VRAM_CONFIG: VramWatchdogConfig = {
  shedFreeGb: 1.5,
  shedLiftPadGb: 0.5,
  floorFreeGb: 0.35,
  floorDropMs: 4000,
  floorLiftPadGb: 0.3,
  floorLiftMs: 4000,
  highWaterFrac: 0.94,
};

/** Persistent watchdog state — feed each step back in (pure step, no clock inside). */
export interface VramCeilingState {
  ceiling: Tier;
  /** Accumulated ms with free at/below the floor (resets on recovery). */
  belowFloorMs: number;
  /** Accumulated ms with lift-worthy headroom while floored (resets when tight). */
  aboveLiftMs: number;
}

export const INITIAL_VRAM_STATE: VramCeilingState = {
  ceiling: "full3d",
  belowFloorMs: 0,
  aboveLiftMs: 0,
};

/**
 * Advance the VRAM ceiling one step. Shedding (full3d→lite3d) is immediate with a
 * GB-pad lift; the 2d floor is time-debounced both ways (see config docs). Fail-open:
 * no/bogus signal → full3d (a machine with no NVML gets the full experience).
 */
export function stepVramCeiling(
  sig: VramSignal | null | undefined,
  dtMs: number,
  st: VramCeilingState = INITIAL_VRAM_STATE,
  cfg: VramWatchdogConfig = DEFAULT_VRAM_CONFIG,
): VramCeilingState {
  if (!sig) return INITIAL_VRAM_STATE;
  const { usedGb, totalGb } = sig;
  if (!(totalGb > 0) || !(usedGb >= 0)) return INITIAL_VRAM_STATE;
  const freeGb = totalGb - usedGb;
  const dt = dtMs > 0 ? dtMs : 0;
  // The near-full fraction is expressed as a free-GB threshold so the shed pad
  // applies to it uniformly (review-caught: a bare `usedFrac >=` test had zero
  // hysteresis and flapped the ceiling on >29 GB cards wobbling one bucket at 0.94).
  const shedAt = Math.max(cfg.shedFreeGb, (1 - cfg.highWaterFrac) * totalGb);

  if (st.ceiling === "2d") {
    if (freeGb > cfg.floorFreeGb + cfg.floorLiftPadGb) {
      const aboveLiftMs = st.aboveLiftMs + dt;
      if (aboveLiftMs >= cfg.floorLiftMs) {
        return { ceiling: "lite3d", belowFloorMs: 0, aboveLiftMs: 0 };
      }
      return { ...st, aboveLiftMs, belowFloorMs: 0 };
    }
    return { ...st, aboveLiftMs: 0 };
  }

  // Not floored. Track sustained floor pressure first (spikes never floor).
  if (freeGb <= cfg.floorFreeGb) {
    const belowFloorMs = st.belowFloorMs + dt;
    if (belowFloorMs >= cfg.floorDropMs) {
      return { ceiling: "2d", belowFloorMs: 0, aboveLiftMs: 0 };
    }
    // While the debounce runs the shed cap already applies (floor < shed threshold).
    return { ceiling: "lite3d", belowFloorMs, aboveLiftMs: 0 };
  }

  // Shed cap: restrict at once, lift only past the pad (anti-flap vs bloom's alloc).
  const shed =
    st.ceiling === "lite3d" ? freeGb <= shedAt + cfg.shedLiftPadGb : freeGb <= shedAt;
  return { ceiling: shed ? "lite3d" : "full3d", belowFloorMs: 0, aboveLiftMs: 0 };
}
