/**
 * Fixed-timestep accumulator (V2, the 60 fps spine). Decouples the simulation from
 * the render rate: a frame advances real time, we run as many FIXED sim steps as
 * fit, and expose an interpolation `alpha` for smooth rendering between steps. This
 * is what makes motion identical at 30 and 60 fps — the same number of sim steps
 * elapse per real second regardless of how often we paint (V2 acceptance).
 *
 * Generalizes renderClock's ad-hoc "cap the active rate" cadence into a correct
 * timestep the 3D sphere (V3) and the motion system (V4) both drive from.
 *
 * Pure + deterministic (no rAF, no clock) so it is unit-tested directly.
 */

export interface FixedTimestep {
  /** Fixed step length in ms (1000 / stepHz). */
  readonly stepMs: number;
  /**
   * Fold in the real elapsed ms since the last call; returns how many fixed steps
   * to run this frame. Large deltas (a backgrounded tab, a breakpoint) are clamped
   * to `maxStepsPerFrame` so we never enter the "spiral of death" trying to catch up.
   */
  advance(deltaMs: number): number;
  /** Interpolation factor in [0, 1) for rendering between the last two sim states. */
  alpha(): number;
  /** Drop any accumulated remainder (e.g. after a hard pause). */
  reset(): void;
}

export function createFixedTimestep(stepHz = 60, maxStepsPerFrame = 5): FixedTimestep {
  const stepMs = 1000 / stepHz;
  const clampMs = stepMs * maxStepsPerFrame;
  let acc = 0;

  return {
    stepMs,
    advance(deltaMs: number): number {
      if (!(deltaMs > 0)) return 0; // NaN / negative / zero clock → no steps
      acc += Math.min(deltaMs, clampMs);
      let steps = 0;
      while (acc >= stepMs && steps < maxStepsPerFrame) {
        acc -= stepMs;
        steps++;
      }
      return steps;
    },
    alpha(): number {
      return acc / stepMs;
    },
    reset(): void {
      acc = 0;
    },
  };
}
