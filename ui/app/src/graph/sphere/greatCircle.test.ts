import { describe, expect, it } from "vitest";
import { arcPoints, slerpUnit } from "./greatCircle";
import type { Vec3 } from "./sphereGeometry";

describe("slerpUnit", () => {
  it("returns the endpoint directions at t=0 and t=1", () => {
    const a: Vec3 = [3, 0, 0];
    const b: Vec3 = [0, 3, 0];
    expect(slerpUnit(a, b, 0)).toEqual([1, 0, 0]);
    const end = slerpUnit(a, b, 1);
    expect(end[0]).toBeCloseTo(0, 6);
    expect(end[1]).toBeCloseTo(1, 6);
  });
});

describe("arcPoints", () => {
  it("starts and ends at the inputs for same-radius endpoints, bowing outward", () => {
    const a: Vec3 = [3, 0, 0];
    const b: Vec3 = [0, 3, 0];
    const pts = arcPoints(a, b, { segments: 12, bulge: 0.2 });
    expect(pts.length).toBe(13);
    expect(pts[0][0]).toBeCloseTo(3, 5);
    expect(pts[12][1]).toBeCloseTo(3, 5);
    const mid = pts[6];
    expect(Math.hypot(...mid)).toBeGreaterThan(3); // bowed out past the surface
  });

  it("degrades an origin-touching edge to a straight radial line", () => {
    const pts = arcPoints([0, 0, 0], [3, 0, 0], { segments: 4 });
    expect(pts[0]).toEqual([0, 0, 0]);
    expect(pts[4][0]).toBeCloseTo(3, 6);
    expect(pts[2]).toEqual([1.5, 0, 0]); // straight midpoint, no bulge
  });

  it("lands on both endpoints when they sit at different radii (inner shell → surface)", () => {
    const a: Vec3 = [0, 0.84, 0]; // router on the inner shell (r = 0.84)
    const b: Vec3 = [3, 0, 0]; // a surface node (r = 3)
    const pts = arcPoints(a, b, { segments: 10, bulge: 0.12 });
    expect(pts[0][0]).toBeCloseTo(0, 5);
    expect(pts[0][1]).toBeCloseTo(0.84, 5);
    expect(pts[10][0]).toBeCloseTo(3, 5);
    expect(pts[10][1]).toBeCloseTo(0, 5);
  });
});
