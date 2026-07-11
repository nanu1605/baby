import { describe, it, expect } from "vitest";
import {
  samplePolyline,
  makeCoalescer,
  makePool,
  pulseProgress,
  flashEnvelope,
  PULSE_MS,
  FLASH_MS,
} from "./pulseAnim";
import type { Vec3 } from "./sphereGeometry";

describe("samplePolyline", () => {
  const line: Vec3[] = [
    [0, 0, 0],
    [10, 0, 0],
  ];
  const bent: Vec3[] = [
    [0, 0, 0],
    [10, 0, 0],
    [10, 10, 0],
  ];

  it("returns origin for an empty polyline", () => {
    expect(samplePolyline([], 0.5)).toEqual([0, 0, 0]);
  });

  it("returns the only point for a single-point polyline", () => {
    expect(samplePolyline([[1, 2, 3]], 0.7)).toEqual([1, 2, 3]);
  });

  it("hits the exact endpoints at t=0 and t=1", () => {
    expect(samplePolyline(line, 0)).toEqual([0, 0, 0]);
    expect(samplePolyline(line, 1)).toEqual([10, 0, 0]);
  });

  it("lerps the midpoint of a 2-point line", () => {
    expect(samplePolyline(line, 0.5)).toEqual([5, 0, 0]);
  });

  it("lands on an interior vertex of a 3-point polyline", () => {
    // t=0.5 → f = 0.5*(3-1) = 1 → exactly the middle vertex
    expect(samplePolyline(bent, 0.5)).toEqual([10, 0, 0]);
    // t=0.25 → f = 0.5 → halfway along the first segment
    expect(samplePolyline(bent, 0.25)).toEqual([5, 0, 0]);
    // t=0.75 → f = 1.5 → halfway along the second segment
    expect(samplePolyline(bent, 0.75)).toEqual([10, 5, 0]);
  });

  it("clamps t outside [0,1] to the endpoints", () => {
    expect(samplePolyline(line, -3)).toEqual([0, 0, 0]);
    expect(samplePolyline(line, 9)).toEqual([10, 0, 0]);
  });

  it("returns finite coordinates", () => {
    const p = samplePolyline(bent, 0.42);
    expect(p.every(Number.isFinite)).toBe(true);
  });
});

describe("makeCoalescer", () => {
  it("allows the first emit and blocks repeats within the window", () => {
    const c = makeCoalescer(150);
    expect(c.allow("a>b", 1000)).toBe(true);
    expect(c.allow("a>b", 1100)).toBe(false); // 100ms < 150
    expect(c.allow("a>b", 1200)).toBe(true); // 200ms ≥ 150
  });

  it("tracks each edge key independently", () => {
    const c = makeCoalescer(150);
    expect(c.allow("a>b", 0)).toBe(true);
    expect(c.allow("c>d", 10)).toBe(true); // different key, not blocked
    expect(c.allow("a>b", 10)).toBe(false);
  });
});

describe("makePool", () => {
  it("hands out distinct slots then returns null on overflow", () => {
    const p = makePool(2);
    const a = p.acquire();
    const b = p.acquire();
    expect(a).not.toBe(b);
    expect(a).not.toBeNull();
    expect(b).not.toBeNull();
    expect(p.acquire()).toBeNull(); // exhausted → drop
  });

  it("reuses a released slot", () => {
    const p = makePool(1);
    const a = p.acquire();
    expect(a).toBe(0);
    expect(p.acquire()).toBeNull();
    p.release(0);
    expect(p.acquire()).toBe(0);
  });

  it("ignores a double release without inflating capacity", () => {
    const p = makePool(1);
    p.acquire();
    p.release(0);
    p.release(0); // no-op the second time
    expect(p.acquire()).toBe(0);
    expect(p.acquire()).toBeNull();
  });
});

describe("pulseProgress", () => {
  it("starts at 0 and is not done", () => {
    expect(pulseProgress(0)).toEqual({ t: 0, done: false });
  });

  it("is done once the arc is fully traversed", () => {
    const life = PULSE_MS / 1000;
    expect(pulseProgress(life).done).toBe(true);
    expect(pulseProgress(life + 1).t).toBe(1);
  });
});

describe("flashEnvelope", () => {
  it("starts full opacity, unit scale, not done", () => {
    const e = flashEnvelope(0);
    expect(e.opacity).toBe(1);
    expect(e.scale).toBe(1);
    expect(e.done).toBe(false);
  });

  it("expands and fades over its life then reports done", () => {
    const half = flashEnvelope(FLASH_MS / 2000);
    expect(half.scale).toBeGreaterThan(1);
    expect(half.opacity).toBeLessThan(1);
    expect(half.opacity).toBeGreaterThan(0);
    expect(flashEnvelope(FLASH_MS / 1000).done).toBe(true);
    expect(flashEnvelope(FLASH_MS / 1000).opacity).toBe(0);
  });
});
