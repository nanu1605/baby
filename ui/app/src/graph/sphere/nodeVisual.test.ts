import { describe, expect, it } from "vitest";
import {
  ACTIVE_EMISSIVE,
  GHOST_EMISSIVE,
  GHOST_OPACITY,
  NODE_EMISSIVE,
  nodeVisualTarget,
  type NodeSignals,
} from "./nodeVisual";

const sig = (over: Partial<NodeSignals> = {}): NodeSignals => ({
  gameMode: false,
  router: "cloud",
  activeBrain: null,
  ...over,
});

describe("nodeVisualTarget — ghost (V3c)", () => {
  it("game mode ghosts the local brain (offloaded → glow washes out)", () => {
    const v = nodeVisualTarget("brain:daily", sig({ gameMode: true }));
    expect(v.ghosted).toBe(true);
    expect(v.emissiveIntensity).toBe(GHOST_EMISSIVE);
    expect(v.opacity).toBe(GHOST_OPACITY);
    // The ghost emissive must sit BELOW the bloom luminanceThreshold (0.55).
    expect(GHOST_EMISSIVE).toBeLessThan(0.55);
  });

  it("no game mode → the local brain glows like any loaded node", () => {
    const v = nodeVisualTarget("brain:daily", sig());
    expect(v.ghosted).toBe(false);
    expect(v.emissiveIntensity).toBe(NODE_EMISSIVE);
    expect(v.opacity).toBe(1);
    expect(v.color).toBeNull();
  });

  it("game mode does NOT ghost cloud brains or anything else", () => {
    for (const id of ["brain:cloud", "brain:nim_primary", "baby_core", "tool:web"]) {
      const v = nodeVisualTarget(id, sig({ gameMode: true }));
      expect(v.ghosted).toBe(false);
    }
  });
});

describe("nodeVisualTarget — router recolor (V3e)", () => {
  it("recolors cloud brains red when offline, amber when degraded", () => {
    expect(nodeVisualTarget("brain:cloud", sig({ router: "offline" })).color).toBe("--red");
    expect(nodeVisualTarget("brain:cloud", sig({ router: "degraded" })).color).toBe("--amber");
    expect(nodeVisualTarget("brain:cloud", sig({ router: "cloud" })).color).toBeNull();
  });

  it("never recolors the daily brain (its honest state is residency, not router)", () => {
    expect(nodeVisualTarget("brain:daily", sig({ router: "offline" })).color).toBeNull();
  });

  it("never recolors non-brain nodes", () => {
    expect(nodeVisualTarget("tool:web", sig({ router: "offline" })).color).toBeNull();
    expect(nodeVisualTarget("baby_core", sig({ router: "degraded" })).color).toBeNull();
  });
});

describe("nodeVisualTarget — active-brain highlight (V3e)", () => {
  it("boosts emissive on the brain that authored the last turn", () => {
    const v = nodeVisualTarget("brain:cloud", sig({ activeBrain: "brain:cloud" }));
    expect(v.emissiveIntensity).toBe(ACTIVE_EMISSIVE);
    expect(ACTIVE_EMISSIVE).toBeGreaterThan(NODE_EMISSIVE);
  });

  it("leaves non-active nodes at the resting emissive", () => {
    const v = nodeVisualTarget("brain:nim_primary", sig({ activeBrain: "brain:cloud" }));
    expect(v.emissiveIntensity).toBe(NODE_EMISSIVE);
  });

  it("ghost outranks highlight for the offloaded local brain", () => {
    const v = nodeVisualTarget("brain:daily", sig({ gameMode: true, activeBrain: "brain:daily" }));
    expect(v.ghosted).toBe(true);
    expect(v.emissiveIntensity).toBe(GHOST_EMISSIVE);
  });
});
