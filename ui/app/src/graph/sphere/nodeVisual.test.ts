import { describe, expect, it } from "vitest";
import {
  GHOST_EMISSIVE,
  GHOST_OPACITY,
  NODE_EMISSIVE,
  nodeVisualTarget,
} from "./nodeVisual";

describe("nodeVisualTarget", () => {
  it("game mode ghosts the local brain (offloaded → glow washes out)", () => {
    const v = nodeVisualTarget("brain:daily", true);
    expect(v.ghosted).toBe(true);
    expect(v.emissiveIntensity).toBe(GHOST_EMISSIVE);
    expect(v.opacity).toBe(GHOST_OPACITY);
    // Honest-data: the ghost emissive must sit BELOW the bloom luminanceThreshold
    // (0.55 in Effects.tsx) so an offloaded model cannot bloom.
    expect(GHOST_EMISSIVE).toBeLessThan(0.55);
  });

  it("no game mode → the local brain glows like any loaded node", () => {
    const v = nodeVisualTarget("brain:daily", false);
    expect(v.ghosted).toBe(false);
    expect(v.emissiveIntensity).toBe(NODE_EMISSIVE);
    expect(v.opacity).toBe(1);
  });

  it("game mode does NOT ghost cloud brains or anything else", () => {
    for (const id of ["brain:cloud", "brain:nim_primary", "brain:nim_heavy", "baby_core", "tool:web"]) {
      const v = nodeVisualTarget(id, true);
      expect(v.ghosted).toBe(false);
      expect(v.emissiveIntensity).toBe(NODE_EMISSIVE);
    }
  });
});
