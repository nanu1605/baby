/**
 * Context-loss retry backoff (V3f). A dead WebGL context (often the local 9B under
 * VRAM pressure, which can hold the GPU for a whole offline turn) would otherwise
 * re-arm a flat 60 s retry forever, churning a Canvas remount once a minute. So the
 * retry backs off on losses that keep RECURRING — 60 s → 2 m → 5 m cap — while a loss
 * that arrives only after a long clean stretch is treated as a fresh, isolated blip
 * and starts back at the 60 s fuse (so a recovered GPU recovers promptly).
 *
 * Escalation keys on the GAP BETWEEN CONSECUTIVE LOSSES, not on "did the remount
 * survive a few seconds": a flaky GPU whose fresh context dies after ~10 s survives any
 * short grace yet is still effectively dead, so a grace-based reset never climbs for it
 * (review-caught). The inter-loss gap is the honest signal — a context that lived a
 * long time between losses genuinely recovered; one that dies again quickly did not.
 *
 * Pure — the store owns the count + last-loss timestamp + timer; this is only the
 * schedule + escalation rule, so it stays vitest-tested with no React/three coupling.
 */

/** A context that lived at least this long between losses counts as recovered. Set
 * well above the 5 min retry cap so the top backoff tier doesn't reset itself. */
export const CTX_STABLE_MS = 600_000; // 10 min clean = recovered → short fuse

/** Retry delay for the Nth consecutive context loss (1-based). Saturates at 5 min. */
export function backoffDelayMs(count: number): number {
  const schedule = [60_000, 120_000, 300_000]; // 60 s → 2 m → 5 m (cap)
  const i = Math.min(Math.max(count, 1) - 1, schedule.length - 1);
  return schedule[i];
}

/**
 * The loss count after a new loss, given the previous count and the gap since the
 * previous loss. A gap ≥ CTX_STABLE_MS (the GPU ran clean for a long stretch) resets to
 * the first 60 s fuse; a quicker repeat climbs, capped at the 5 min tier (count 3).
 */
export function nextLossCount(
  prevCount: number,
  gapMs: number,
  stableMs = CTX_STABLE_MS,
): number {
  if (gapMs >= stableMs) return 1;
  return Math.min(prevCount + 1, 3);
}
