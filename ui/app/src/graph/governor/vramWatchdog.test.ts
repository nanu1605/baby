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

  it("local 9B resident → pressure via the high-water fraction", () => {
    // 7.6 / 8 = 0.95 >= 0.80
    expect(vramPressured({ usedGb: 7.6, totalGb: 8 })).toBe(true);
  });

  it("tight free headroom → pressure via minFreeGb", () => {
    // free = 8 - 6.6 = 1.4 <= 1.5, even though the fraction (0.825) is only just over
    expect(vramPressured({ usedGb: 6.6, totalGb: 8 })).toBe(true);
  });

  it("guards against a bogus signal", () => {
    expect(vramPressured({ usedGb: 1, totalGb: 0 })).toBe(false);
    expect(vramPressured({ usedGb: -1, totalGb: 8 })).toBe(false);
  });

  it("respects a custom config", () => {
    const strict = { highWaterFrac: 0.5, minFreeGb: 0 };
    expect(vramPressured({ usedGb: 5, totalGb: 8 }, strict)).toBe(true); // 0.625 >= 0.5
    expect(DEFAULT_VRAM_CONFIG.highWaterFrac).toBe(0.8);
  });
});
