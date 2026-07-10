import { describe, expect, it } from "vitest";
import { motionLevel } from "./motion";

describe("motionLevel", () => {
  it("full3d with no dampers = full", () => {
    expect(motionLevel(false, false, "full3d")).toBe("full");
  });

  it("lite3d = lite (essential motion only)", () => {
    expect(motionLevel(false, false, "lite3d")).toBe("lite");
  });

  it("the 2d floor = off", () => {
    expect(motionLevel(false, false, "2d")).toBe("off");
  });

  it("prefers-reduced-motion forces off at every tier", () => {
    expect(motionLevel(true, false, "full3d")).toBe("off");
    expect(motionLevel(true, false, "lite3d")).toBe("off");
    expect(motionLevel(true, false, "2d")).toBe("off");
  });

  it("performanceMode forces off at every tier", () => {
    expect(motionLevel(false, true, "full3d")).toBe("off");
    expect(motionLevel(false, true, "lite3d")).toBe("off");
  });
});
