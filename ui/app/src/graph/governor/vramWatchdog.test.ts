import { describe, expect, it } from "vitest";
import { modelCeiling } from "./vramWatchdog";

describe("modelCeiling", () => {
  it("local model resident → lite3d (shed bloom, keep the sphere, leave LLM headroom)", () => {
    expect(modelCeiling(true)).toBe("lite3d");
  });

  it("not resident → full3d (game-mode offload / idle-expired both land here)", () => {
    expect(modelCeiling(false)).toBe("full3d");
  });

  it("unknown fails open to full3d (no local provider, ollama down, signal not yet seen)", () => {
    expect(modelCeiling(null)).toBe("full3d");
    expect(modelCeiling(undefined)).toBe("full3d");
  });
});
