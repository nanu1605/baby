import { describe, expect, it } from "vitest";
import {
  CTX_STABLE_MS,
  backoffDelayMs,
  nextLossCount,
} from "./contextLossBackoff";

describe("backoffDelayMs", () => {
  it("climbs 60s → 2m → 5m on consecutive losses", () => {
    expect(backoffDelayMs(1)).toBe(60_000);
    expect(backoffDelayMs(2)).toBe(120_000);
    expect(backoffDelayMs(3)).toBe(300_000);
  });

  it("saturates at the 5m cap for further losses", () => {
    expect(backoffDelayMs(4)).toBe(300_000);
    expect(backoffDelayMs(10)).toBe(300_000);
  });

  it("treats a reset count (0 or 1) as the short 60s fuse", () => {
    expect(backoffDelayMs(0)).toBe(60_000);
    expect(backoffDelayMs(1)).toBe(60_000);
  });
});

describe("nextLossCount", () => {
  it("climbs while losses keep recurring within the stable window", () => {
    // A flaky GPU losing its context every ~70s: each gap is well under CTX_STABLE_MS,
    // so the count climbs and the retry quiesces toward the 5m cap.
    expect(nextLossCount(0, 70_000)).toBe(1);
    expect(nextLossCount(1, 70_000)).toBe(2);
    expect(nextLossCount(2, 70_000)).toBe(3);
    expect(nextLossCount(3, 70_000)).toBe(3); // capped at the 5m tier
  });

  it("resets to the 60s fuse after a long clean gap (recovered GPU)", () => {
    // A loss that arrives only after the GPU ran clean past the stable window is an
    // isolated blip, not sustained failure → back to the short fuse.
    expect(nextLossCount(3, CTX_STABLE_MS)).toBe(1);
    expect(nextLossCount(3, CTX_STABLE_MS + 1)).toBe(1);
    expect(nextLossCount(2, Infinity)).toBe(1); // first-ever loss (no prior timestamp)
  });

  it("does NOT reset for a gap just under the stable window", () => {
    expect(nextLossCount(2, CTX_STABLE_MS - 1)).toBe(3);
  });
});
