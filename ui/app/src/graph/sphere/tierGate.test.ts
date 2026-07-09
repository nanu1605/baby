import { describe, expect, it } from "vitest";
import { tierToRender } from "./tierGate";
import { effectiveCeiling } from "../governor/tierMachine";

describe("tierToRender", () => {
  it("full3d = sphere + bloom + particles, no 2d floor", () => {
    expect(tierToRender("full3d")).toEqual({
      sphere: true,
      bloom: true,
      particles: true,
      floor2d: false,
    });
  });

  it("lite3d keeps the sphere but sheds bloom + particles", () => {
    expect(tierToRender("lite3d")).toEqual({
      sphere: true,
      bloom: false,
      particles: false,
      floor2d: false,
    });
  });

  it("2d drops the sphere and renders the 2D floor", () => {
    expect(tierToRender("2d")).toEqual({
      sphere: false,
      bloom: false,
      particles: false,
      floor2d: true,
    });
  });
});

describe("effectiveCeiling (ui.brain + render.tier fold)", () => {
  it("ui.brain:2d forces the 2d floor regardless of render.tier", () => {
    expect(effectiveCeiling("2d", "auto")).toBe("2d");
    expect(effectiveCeiling("2d", "full3d")).toBe("2d");
  });

  it("ui.brain:3d defers to render.tier", () => {
    expect(effectiveCeiling("3d", "auto")).toBe("full3d");
    expect(effectiveCeiling("3d", "lite3d")).toBe("lite3d");
    expect(effectiveCeiling("3d", "2d")).toBe("2d");
  });

  it("defaults to full3d ceiling when unset", () => {
    expect(effectiveCeiling(undefined, undefined)).toBe("full3d");
  });
});
