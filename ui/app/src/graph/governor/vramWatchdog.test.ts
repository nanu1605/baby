import { describe, expect, it } from "vitest";
import { DEFAULT_VRAM_CONFIG, vramPressured } from "./vramWatchdog";

describe("vramWatchdog", () => {
  it("no signal → no pressure (full experience on a machine with no NVML)", () => {
    expect(vramPressured(null)).toBe(false);
    expect(vramPressured(undefined)).toBe(false);
  });

  it("idle GPU → no pressure", () => {
    expect(vramPressured({ usedGb: 1.0, totalGb: 8 })).toBe(false);
    expect(vramPressured({ usedGb: 5.0, totalGb: 8 })).toBe(false);
  });

  it("8 GB card, local 9B resident → pressure (no free headroom left)", () => {
    // free = 8 - 7.6 = 0.4 <= 1.5 → the small card genuinely can't fit 3D too.
    expect(vramPressured({ usedGb: 7.6, totalGb: 8 })).toBe(true);
  });

  it("16 GB card, local 9B resident → NO pressure (the sphere is the centerpiece)", () => {
    // free = 16 - 9 = 7 GB, far above minFreeGb → sphere stays.
    expect(vramPressured({ usedGb: 9, totalGb: 16 })).toBe(false);
    // The actual regression: a loaded 16 GB card at 13 GB used still has 3 GB free —
    // plenty for the light sphere. The old 8 GB-tuned 0.8 fraction (13/16 = 0.81 ≥ 0.8)
    // wrongly demoted this to 2D; the headroom gate (3 > 1.5, 0.81 < 0.94) keeps 3D.
    expect(vramPressured({ usedGb: 13, totalGb: 16 })).toBe(false);
  });

  it("16 GB card genuinely full (e.g. gaming) → pressure, GPU yields", () => {
    // free = 16 - 15 = 1 <= 1.5 → yield.
    expect(vramPressured({ usedGb: 15, totalGb: 16 })).toBe(true);
  });

  it("tight free headroom → pressure via minFreeGb", () => {
    // free = 8 - 6.6 = 1.4 <= 1.5.
    expect(vramPressured({ usedGb: 6.6, totalGb: 8 })).toBe(true);
  });

  it("pins the primary gate's exact boundary (free == 1.5 is a reachable 0.25-GB bucket)", () => {
    // vram arrives quantized to 0.25 GB buckets, so free == exactly 1.5 happens; the
    // gate is inclusive (<=). Neighbouring bucket (free 1.75) stays calm.
    expect(vramPressured({ usedGb: 6.5, totalGb: 8 })).toBe(true); // free = 1.5
    expect(vramPressured({ usedGb: 14.5, totalGb: 16 })).toBe(true); // free = 1.5
    expect(vramPressured({ usedGb: 14.25, totalGb: 16 })).toBe(false); // free = 1.75
  });

  it("very large card: the near-full fraction fires on its own (gates diverge above 25 GB total)", () => {
    // 46/48 → usedFrac 0.958 >= 0.94 while free = 2 > minFreeGb — only the fraction
    // gate trips. Just under it (44/48 → 0.917, free 4) stays calm.
    expect(vramPressured({ usedGb: 46, totalGb: 48 })).toBe(true);
    expect(vramPressured({ usedGb: 44, totalGb: 48 })).toBe(false);
  });

  it("guards against a bogus signal", () => {
    expect(vramPressured({ usedGb: 1, totalGb: 0 })).toBe(false);
    expect(vramPressured({ usedGb: -1, totalGb: 8 })).toBe(false);
  });

  it("respects a custom config", () => {
    const strict = { highWaterFrac: 0.5, minFreeGb: 0 };
    expect(vramPressured({ usedGb: 5, totalGb: 8 }, strict)).toBe(true); // 0.625 >= 0.5
    expect(DEFAULT_VRAM_CONFIG.highWaterFrac).toBe(0.94);
    expect(DEFAULT_VRAM_CONFIG.minFreeGb).toBe(1.5);
  });
});
