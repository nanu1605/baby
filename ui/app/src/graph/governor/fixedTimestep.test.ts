import { describe, expect, it } from "vitest";
import { createFixedTimestep } from "./fixedTimestep";

describe("fixedTimestep", () => {
  it("runs the same number of sim steps per second at 30 and 60 fps", () => {
    const at = (frames: number, dt: number) => {
      const ts = createFixedTimestep(60);
      let steps = 0;
      for (let i = 0; i < frames; i++) steps += ts.advance(dt);
      return steps;
    };
    const sixty = at(60, 1000 / 60);
    const thirty = at(30, 1000 / 30);
    expect(sixty).toBe(60);
    expect(thirty).toBe(60); // identity: fps changes, sim rate does not
  });

  it("clamps a huge delta so it never spirals to catch up", () => {
    const ts = createFixedTimestep(60, 5);
    const steps = ts.advance(10_000); // ~600 steps unclamped
    expect(steps).toBeGreaterThan(0);
    expect(steps).toBeLessThanOrEqual(5); // hard cap at maxStepsPerFrame
  });

  it("exposes an interpolation alpha in [0, 1)", () => {
    const ts = createFixedTimestep(60);
    ts.advance(8); // ~half a 16.67ms step
    const a = ts.alpha();
    expect(a).toBeGreaterThan(0);
    expect(a).toBeLessThan(1);
  });

  it("ignores a stopped / bogus clock", () => {
    const ts = createFixedTimestep(60);
    expect(ts.advance(0)).toBe(0);
    expect(ts.advance(-5)).toBe(0);
    expect(ts.advance(Number.NaN)).toBe(0);
  });

  it("reset() drops the accumulated remainder", () => {
    const ts = createFixedTimestep(60);
    ts.advance(8);
    ts.reset();
    expect(ts.alpha()).toBe(0);
  });
});
