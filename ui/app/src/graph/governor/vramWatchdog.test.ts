import { describe, expect, it } from "vitest";
import {
  DEFAULT_VRAM_CONFIG,
  INITIAL_VRAM_STATE,
  stepVramCeiling,
  type VramCeilingState,
  type VramSignal,
} from "./vramWatchdog";

/** Step the machine with a constant signal for `ms` in ws-tick-sized chunks. */
function run(
  sig: VramSignal | null,
  ms: number,
  st: VramCeilingState = INITIAL_VRAM_STATE,
): VramCeilingState {
  let s = st;
  for (let t = 0; t < ms; t += 1500) {
    s = stepVramCeiling(sig, Math.min(1500, ms - t), s);
  }
  return s;
}

describe("stepVramCeiling", () => {
  it("no signal → full3d, even from a floored state (fail-open, no NVML)", () => {
    expect(stepVramCeiling(null, 16, INITIAL_VRAM_STATE).ceiling).toBe("full3d");
    const floored: VramCeilingState = { ceiling: "2d", belowFloorMs: 0, aboveLiftMs: 0 };
    expect(stepVramCeiling(undefined, 16, floored).ceiling).toBe("full3d");
  });

  it("guards against a bogus signal", () => {
    expect(stepVramCeiling({ usedGb: 1, totalGb: 0 }, 16).ceiling).toBe("full3d");
    expect(stepVramCeiling({ usedGb: -1, totalGb: 8 }, 16).ceiling).toBe("full3d");
  });

  it("idle GPU → full3d", () => {
    expect(stepVramCeiling({ usedGb: 1.0, totalGb: 8 }, 16).ceiling).toBe("full3d");
    expect(stepVramCeiling({ usedGb: 9.0, totalGb: 16 }, 16).ceiling).toBe("full3d");
  });

  it("owner's box, 9B warm → lite3d immediately (shed bloom, KEEP the sphere)", () => {
    // Live reading: used 7.88 / total 8.55 → free 0.67 <= 1.5, > 0.35.
    expect(stepVramCeiling({ usedGb: 7.88, totalGb: 8.55 }, 16).ceiling).toBe("lite3d");
  });

  it("a model-reload spike (seconds at the floor) NEVER floors — only sustained does", () => {
    const spike = { usedGb: 8.35, totalGb: 8.55 }; // free 0.2 <= 0.35
    // 3 s of spike: still lite3d (the shed cap covers it; frame governor guards fps).
    const during = run(spike, 3000);
    expect(during.ceiling).toBe("lite3d");
    // Spike ends before the 4 s debounce → the timer resets, never floored.
    const after = stepVramCeiling({ usedGb: 7.71, totalGb: 8.55 }, 1500, during);
    expect(after.ceiling).toBe("lite3d");
    expect(after.belowFloorMs).toBe(0);
    // But genuinely sustained (>= 4 s) → the honest 2d floor.
    expect(run(spike, 6000).ceiling).toBe("2d");
  });

  it("owner regression: floored box parked at free 0.84 RECOVERS (time, not GB pads)", () => {
    // Two successive GB-pad designs stranded this exact box at free 1.08 and 0.84.
    // Lift needs free > 0.35 + 0.3 (canvas re-alloc) sustained 4 s — 0.84 qualifies.
    const floored: VramCeilingState = { ceiling: "2d", belowFloorMs: 0, aboveLiftMs: 0 };
    const sig = { usedGb: 7.71, totalGb: 8.55 }; // free 0.84
    expect(run(sig, 3000, floored).ceiling).toBe("2d"); // debouncing…
    expect(run(sig, 6000, floored).ceiling).toBe("lite3d"); // …recovered
  });

  it("stays floored when headroom could not absorb the canvas re-alloc", () => {
    // free 0.6 <= 0.35 + 0.3 → lifting would re-breach the floor → hold 2d.
    const floored: VramCeilingState = { ceiling: "2d", belowFloorMs: 0, aboveLiftMs: 0 };
    const s = run({ usedGb: 7.95, totalGb: 8.55 }, 20000, floored);
    expect(s.ceiling).toBe("2d");
    expect(s.aboveLiftMs).toBe(0);
  });

  it("shed cap restricts at once, lifts only past the pad (anti-flap vs bloom alloc)", () => {
    // full3d → lite3d immediately at free 1.3.
    const shed = stepVramCeiling({ usedGb: 7.25, totalGb: 8.55 }, 16);
    expect(shed.ceiling).toBe("lite3d");
    // free 1.7 <= 1.5 + 0.5 → holds lite3d…
    expect(stepVramCeiling({ usedGb: 6.85, totalGb: 8.55 }, 16, shed).ceiling).toBe("lite3d");
    // …free 2.5 > 2.0 → lifts.
    expect(stepVramCeiling({ usedGb: 6.05, totalGb: 8.55 }, 16, shed).ceiling).toBe("full3d");
  });

  it("shed boundary is inclusive; 16 GB with a 9B stays full3d", () => {
    expect(stepVramCeiling({ usedGb: 6.5, totalGb: 8 }, 16).ceiling).toBe("lite3d"); // free 1.5
    expect(stepVramCeiling({ usedGb: 14.25, totalGb: 16 }, 16).ceiling).toBe("full3d"); // 1.75
  });

  it("near-full on a very large card sheds AND carries the same lift pad (no flap)", () => {
    // shedAt = (1 - 0.94) * 48 = 2.88 — expressed in free-GB so the pad applies.
    const shed = stepVramCeiling({ usedGb: 45.25, totalGb: 48 }, 16); // free 2.75
    expect(shed.ceiling).toBe("lite3d");
    // One bucket back up (free 3.0 <= 2.88 + 0.5) HOLDS…
    expect(stepVramCeiling({ usedGb: 45.0, totalGb: 48 }, 16, shed).ceiling).toBe("lite3d");
    // …lifting only with real headroom (free 3.75 > 3.38).
    expect(stepVramCeiling({ usedGb: 44.25, totalGb: 48 }, 16, shed).ceiling).toBe("full3d");
  });

  it("pins the default thresholds", () => {
    expect(DEFAULT_VRAM_CONFIG.shedFreeGb).toBe(1.5);
    expect(DEFAULT_VRAM_CONFIG.shedLiftPadGb).toBe(0.5);
    expect(DEFAULT_VRAM_CONFIG.floorFreeGb).toBe(0.35);
    expect(DEFAULT_VRAM_CONFIG.floorDropMs).toBe(4000);
    expect(DEFAULT_VRAM_CONFIG.floorLiftPadGb).toBe(0.3);
    expect(DEFAULT_VRAM_CONFIG.floorLiftMs).toBe(4000);
    expect(DEFAULT_VRAM_CONFIG.highWaterFrac).toBe(0.94);
  });
});
