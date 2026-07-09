/**
 * Idle-throttled render clock (B3). The core gauge breathes on the canvas, but
 * we must not burn 60fps at idle (GPU belongs to the LLM).
 *
 * We own the only draw loop: the force-graph engine loop is left paused, and each
 * of our rAF ticks forces exactly ONE repaint via `draw()` (a
 * resumeAnimation()→pauseAnimation() one-shot, with `autoPauseRedraw=false` so the
 * frame always paints). Cadence:
 *   - active (a turn running or a pulse in flight): capped to ~60fps (NOT the
 *     display's native rate — a 144Hz panel would otherwise draw at 144fps)
 *   - idle: gated to ~20–24fps (breathing still looks right — phase comes from
 *     performance.now(), not frame count)
 *   - low-power (performance_mode / prefers-reduced-motion) + idle: no draws at all
 *   - tab hidden: no draws (hard pause; rAF is already throttled by the browser)
 */

export interface RenderClockOpts {
  /** true → draw at the active cap (~60fps). */
  getActive: () => boolean;
  /** true → when idle, don't draw at all (canvas fully quiet). */
  isLowPower: () => boolean;
  /** Force one repaint (resume→pause one-shot). */
  draw: () => void;
}

const ACTIVE_INTERVAL_MS = 1000 / 60;
const IDLE_INTERVAL_MS = 1000 / 22;

export function startRenderClock(opts: RenderClockOpts): () => void {
  let raf = 0;
  let lastDraw = 0;
  let stopped = false;

  const tick = (now: number) => {
    if (stopped) return;
    raf = requestAnimationFrame(tick);

    // Hard-pause when the tab is hidden.
    if (typeof document !== "undefined" && document.visibilityState === "hidden") {
      return;
    }

    const active = opts.getActive();
    if (!active && opts.isLowPower()) return; // idle + low-power → canvas quiet

    // Frame-gate BOTH states so the active rate is a true 60fps cap, independent
    // of the monitor's refresh (120/144Hz panels would otherwise overshoot).
    const interval = active ? ACTIVE_INTERVAL_MS : IDLE_INTERVAL_MS;
    if (now - lastDraw < interval) return;
    lastDraw = now;
    opts.draw();
  };

  raf = requestAnimationFrame(tick);

  return () => {
    stopped = true;
    cancelAnimationFrame(raf);
  };
}
