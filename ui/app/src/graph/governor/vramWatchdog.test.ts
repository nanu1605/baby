import { describe, expect, it } from "vitest";
import { DEFAULT_VRAM_CONFIG, vramCeiling } from "./vramWatchdog";

describe("vramCeiling", () => {
  it("no signal → full3d (full experience on a machine with no NVML)", () => {
    expect(vramCeiling(null)).toBe("full3d");
    expect(vramCeiling(undefined)).toBe("full3d");
  });

  it("idle GPU → full3d", () => {
    expect(vramCeiling({ usedGb: 1.0, totalGb: 8 })).toBe("full3d");
    expect(vramCeiling({ usedGb: 9.0, totalGb: 16 })).toBe("full3d"); // 16 GB + 9B: 7 free
  });

  it("owner's real box: 8 GB card + warm 9B → lite3d, NOT the 2d floor", () => {
    // Live /stats reading 2026-07-10: used 7.88 / total 8.55 → free 0.67. Tight
    // enough to shed bloom (<= 1.5) but the lite sphere still fits (> 0.5): the
    // centerpiece stays up in the daily game-mode-off state.
    expect(vramCeiling({ usedGb: 7.88, totalGb: 8.55 })).toBe("lite3d");
  });

  it("genuinely no room → the 2d floor", () => {
    // free = 0.30 <= floorFreeGb 0.35.
    expect(vramCeiling({ usedGb: 8.25, totalGb: 8.55 })).toBe("2d");
    // free = 0.40 still fits the lite sphere (> 0.35) → shed only.
    expect(vramCeiling({ usedGb: 8.15, totalGb: 8.55 })).toBe("lite3d");
  });

  it("shed boundary is inclusive (free == 1.5, a reachable 0.25-GB bucket)", () => {
    expect(vramCeiling({ usedGb: 6.5, totalGb: 8 })).toBe("lite3d"); // free = 1.5
    expect(vramCeiling({ usedGb: 14.25, totalGb: 16 })).toBe("full3d"); // free = 1.75
  });

  it("near-full fraction sheds on its own on a very large card", () => {
    // 46/48 → usedFrac 0.958 >= 0.94 while free = 2 > shedFreeGb.
    expect(vramCeiling({ usedGb: 46, totalGb: 48 })).toBe("lite3d");
    expect(vramCeiling({ usedGb: 44, totalGb: 48 })).toBe("full3d"); // 0.917, free 4
  });

  it("restricts immediately, lifts only past the hysteresis pad", () => {
    // Drop: full3d → lite3d as soon as free <= 1.5.
    expect(vramCeiling({ usedGb: 7.25, totalGb: 8.55 }, "full3d")).toBe("lite3d");
    // The 0.5 pad must exceed the promoted tier's OWN footprint (bloom composer at
    // multisampling 0 ~0.15, canvas remount ~0.3), else lift → allocate → shed loops
    // forever (review-caught): free 1.7 and 1.95 (inside 1.5 + 0.5) HOLD lite3d…
    expect(vramCeiling({ usedGb: 6.85, totalGb: 8.55 }, "lite3d")).toBe("lite3d");
    expect(vramCeiling({ usedGb: 6.6, totalGb: 8.55 }, "lite3d")).toBe("lite3d");
    // …and it lifts once free clears the pad (2.5 > 2.0).
    expect(vramCeiling({ usedGb: 6.05, totalGb: 8.55 }, "lite3d")).toBe("full3d");
    // At the floor the lift point is free > 0.85: 0.5 and 0.65 hold 2d, 1.25 lifts.
    expect(vramCeiling({ usedGb: 8.05, totalGb: 8.55 }, "2d")).toBe("2d");
    expect(vramCeiling({ usedGb: 7.9, totalGb: 8.55 }, "2d")).toBe("2d"); // free 0.65
    expect(vramCeiling({ usedGb: 7.3, totalGb: 8.55 }, "2d")).toBe("lite3d"); // free 1.25
    // Owner regression (live-caught): after a model-reload spike floors the ceiling,
    // the box settles near free 1.05–1.1 — that MUST recover off the 2d floor
    // (a 0.75 pad put the lift point at 1.10 and stranded it at free 1.08).
    expect(vramCeiling({ usedGb: 7.5, totalGb: 8.55 }, "2d")).toBe("lite3d"); // free 1.05
  });

  it("the near-full threshold carries the SAME hysteresis (48 GB card at 0.94)", () => {
    // Review-caught: a bare `usedFrac >=` test had zero pad, so a one-bucket wobble
    // straddling 0.94 flapped the ceiling every sample on large cards. As free-GB:
    // shedAt = (1 - 0.94) * 48 = 2.88.
    expect(vramCeiling({ usedGb: 45.25, totalGb: 48 }, "full3d")).toBe("lite3d"); // free 2.75
    // One bucket back up (free 3.0 <= 2.88 + 0.5) HOLDS — no flap…
    expect(vramCeiling({ usedGb: 45.0, totalGb: 48 }, "lite3d")).toBe("lite3d");
    // …lifting only with real headroom (free 3.75 > 3.38).
    expect(vramCeiling({ usedGb: 44.25, totalGb: 48 }, "lite3d")).toBe("full3d");
  });

  it("a signal loss mid-session fails open to full3d", () => {
    expect(vramCeiling(null, "2d")).toBe("full3d");
  });

  it("guards against a bogus signal", () => {
    expect(vramCeiling({ usedGb: 1, totalGb: 0 })).toBe("full3d");
    expect(vramCeiling({ usedGb: -1, totalGb: 8 })).toBe("full3d");
  });

  it("pins the default thresholds", () => {
    expect(DEFAULT_VRAM_CONFIG.shedFreeGb).toBe(1.5);
    expect(DEFAULT_VRAM_CONFIG.floorFreeGb).toBe(0.35);
    expect(DEFAULT_VRAM_CONFIG.hysteresisGb).toBe(0.5);
    expect(DEFAULT_VRAM_CONFIG.highWaterFrac).toBe(0.94);
  });
});
