/**
 * Quality-tier state machine (V2). Three tiers, highest to lowest:
 *   full3d  → the full neural sphere (bloom, particles, all effects)
 *   lite3d  → sphere without the expensive post/particles
 *   2d      → the v3 canvas graph — the always-available floor
 *
 * HYSTERESIS, mirroring the router's demote-fast / recover-slow state machine:
 * a short sustained pressure demotes immediately (protect the 60 fps contract),
 * but promotion needs a long stretch of calm so we never oscillate on a brief dip.
 * "Pressure" is supplied by the caller (frame-budget overrun OR the VRAM watchdog);
 * this module only owns the timing + the ceiling, and is pure so it unit-tests
 * without a clock.
 */

export type Tier = "full3d" | "lite3d" | "2d";

/** Low → high; index is the rank used for demote/promote. */
export const TIER_ORDER: Tier[] = ["2d", "lite3d", "full3d"];

export function rankOf(t: Tier): number {
  return TIER_ORDER.indexOf(t);
}

/** Map the render.tier config string to a ceiling tier. "auto" = full3d. */
export function ceilingFromConfig(tier: string | undefined): Tier {
  switch (tier) {
    case "2d":
      return "2d";
    case "lite3d":
      return "lite3d";
    default:
      return "full3d"; // "auto", "full3d", or anything unknown → no cap
  }
}

export interface TierConfig {
  /** Sustained-pressure ms before a demote. Small — protect frames fast. */
  demoteAfterMs: number;
  /** Sustained-calm ms before a promote. Large — recover slowly, no flapping. */
  promoteAfterMs: number;
  /** Config ceiling: render.tier "auto" → full3d; "lite3d"/"2d" cap it lower. */
  ceiling: Tier;
}

export const DEFAULT_TIER_CONFIG: TierConfig = {
  demoteAfterMs: 250,
  promoteAfterMs: 4000,
  ceiling: "full3d",
};

export interface TierState {
  tier: Tier;
  /** Accumulated ms of pressure at the current tier (resets on any calm frame). */
  stressMs: number;
  /** Accumulated ms of calm at the current tier (resets on any pressured frame). */
  calmMs: number;
}

export function initialTierState(cfg: TierConfig = DEFAULT_TIER_CONFIG): TierState {
  return { tier: capTo(cfg.ceiling, cfg.ceiling), stressMs: 0, calmMs: 0 };
}

/** Never exceed the ceiling; never drop below the 2d floor. */
function capTo(tier: Tier, ceiling: Tier): Tier {
  const r = Math.min(rankOf(tier), rankOf(ceiling));
  return TIER_ORDER[Math.max(0, r)];
}

export interface TierInput {
  /** True this frame if the frame budget was blown OR the VRAM watchdog fired. */
  pressured: boolean;
  /** Real ms elapsed since the last step. */
  dtMs: number;
  cfg?: TierConfig;
}

/**
 * Advance the machine one frame. Demotes after `demoteAfterMs` of sustained
 * pressure; promotes after `promoteAfterMs` of sustained calm; always snaps down to
 * the ceiling immediately if the config lowered it. Returns a NEW state.
 */
export function stepTier(state: TierState, input: TierInput): TierState {
  const cfg = input.cfg ?? DEFAULT_TIER_CONFIG;
  const dt = input.dtMs > 0 ? input.dtMs : 0;

  // A lowered ceiling wins now — no waiting.
  const ceilingCapped = capTo(state.tier, cfg.ceiling);
  if (ceilingCapped !== state.tier) {
    return { tier: ceilingCapped, stressMs: 0, calmMs: 0 };
  }

  if (input.pressured) {
    const stressMs = state.stressMs + dt;
    if (stressMs >= cfg.demoteAfterMs && rankOf(state.tier) > 0) {
      return { tier: TIER_ORDER[rankOf(state.tier) - 1], stressMs: 0, calmMs: 0 };
    }
    return { tier: state.tier, stressMs, calmMs: 0 };
  }

  const calmMs = state.calmMs + dt;
  if (calmMs >= cfg.promoteAfterMs && rankOf(state.tier) < rankOf(cfg.ceiling)) {
    return { tier: TIER_ORDER[rankOf(state.tier) + 1], stressMs: 0, calmMs: 0 };
  }
  return { tier: state.tier, stressMs: 0, calmMs };
}
