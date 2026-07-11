import { describe, expect, it } from "vitest";
import {
  DEFAULT_TIER_CONFIG,
  initialTierState,
  stepTier,
  type TierConfig,
  type TierState,
} from "./tierMachine";

const CFG: TierConfig = DEFAULT_TIER_CONFIG; // demote 250ms, promote 4000ms, ceiling full3d

function press(s: TierState, dtMs: number, cfg = CFG): TierState {
  return stepTier(s, { pressured: true, dtMs, cfg });
}
function calm(s: TierState, dtMs: number, cfg = CFG): TierState {
  return stepTier(s, { pressured: false, dtMs, cfg });
}

describe("tierMachine", () => {
  it("starts at the ceiling", () => {
    expect(initialTierState().tier).toBe("full3d");
  });

  it("demotes after sustained pressure, one tier at a time, down to the 2d floor", () => {
    let s = initialTierState();
    s = press(s, 250); // full3d -> lite3d
    expect(s.tier).toBe("lite3d");
    s = press(s, 250); // lite3d -> 2d
    expect(s.tier).toBe("2d");
    s = press(s, 250); // already floor, stays
    expect(s.tier).toBe("2d");
  });

  it("demotes fast but promotes slow (hysteresis, no flapping)", () => {
    let s = initialTierState();
    s = press(s, 250); // demoted quickly
    expect(s.tier).toBe("lite3d");

    // A short calm must NOT promote (needs promoteAfterMs = 4000).
    s = calm(s, 250);
    expect(s.tier).toBe("lite3d");
    // Sustained calm eventually promotes.
    s = calm(s, 4000);
    expect(s.tier).toBe("full3d");
  });

  it("does not demote on a brief blip under demoteAfterMs, and calm resets the stress", () => {
    let s = initialTierState();
    s = press(s, 100); // 100 < 250, no demote
    expect(s.tier).toBe("full3d");
    expect(s.stressMs).toBe(100);
    s = calm(s, 10); // any calm frame wipes accumulated stress
    expect(s.stressMs).toBe(0);
    s = press(s, 200); // starts over, 200 < 250, still no demote
    expect(s.tier).toBe("full3d");
  });

  it("snaps down immediately when the config ceiling is lowered", () => {
    let s = initialTierState();
    const capped: TierConfig = { ...CFG, ceiling: "2d" };
    s = stepTier(s, { pressured: false, dtMs: 1, cfg: capped });
    expect(s.tier).toBe("2d"); // no waiting for demoteAfterMs
  });

  it("never promotes above the ceiling", () => {
    const cfg: TierConfig = { ...CFG, ceiling: "lite3d" };
    let s = initialTierState(cfg); // starts capped at lite3d
    expect(s.tier).toBe("lite3d");
    s = calm(s, 8000, cfg); // lots of calm
    expect(s.tier).toBe("lite3d"); // cannot reach full3d
  });
});
