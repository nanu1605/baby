/**
 * Per-node visual targets (V3c ghost + V3e recolor/highlight): honest-data — a
 * node's glow/colour must reflect its REAL state.
 *  - Game mode offloads the local 9B → `brain:daily` ghosts: emissive drops below
 *    the bloom threshold (glow washes out) and the body fades (mirrors the 2D
 *    dashed ghost).
 *  - Router health recolours the CLOUD brains: degraded → amber, offline → red
 *    (the local daily brain is governed by the ghost, not router health).
 *  - The brain that authored the last turn (`activeBrain`) gets an emissive boost.
 *
 * Pure — vitest-tested, no three import (returns a CSS-var name; the render
 * boundary resolves it to a THREE.Color).
 */
import type { RouterHealth } from "../../types";

/** Resting emissive for a loaded node — HDR punch above the bloom threshold. */
export const NODE_EMISSIVE = 1.35;
/** Ghost emissive — far below luminanceThreshold 0.55 so bloom fully washes out. */
export const GHOST_EMISSIVE = 0.06;
/** Ghost body opacity — echoes the 2D graph's 0.28 ghost alpha. */
export const GHOST_OPACITY = 0.3;
/** Active-brain highlight — a brighter core than a resting loaded node. */
export const ACTIVE_EMISSIVE = 2.4;

export interface NodeSignals {
  gameMode: boolean;
  router: RouterHealth;
  activeBrain: string | null;
}

export interface NodeVisualTarget {
  emissiveIntensity: number;
  opacity: number;
  ghosted: boolean;
  /** CSS-var name to recolour toward, or null = keep the node's base type colour. */
  color: string | null;
}

/** Visual target for a node given the live signals. */
export function nodeVisualTarget(nodeId: string, sig: NodeSignals): NodeVisualTarget {
  const { gameMode, router, activeBrain } = sig;

  // Local brain offloaded in game mode → ghost (outranks recolor/highlight).
  if (gameMode && nodeId === "brain:daily") {
    return {
      emissiveIntensity: GHOST_EMISSIVE,
      opacity: GHOST_OPACITY,
      ghosted: true,
      color: null,
    };
  }

  // Router-health recolor applies to cloud brains only (the daily brain's honest
  // state is residency/ghost, not connectivity).
  let color: string | null = null;
  if (nodeId.startsWith("brain:") && nodeId !== "brain:daily") {
    if (router === "offline") color = "--red";
    else if (router === "degraded") color = "--amber";
  }

  const active = activeBrain != null && nodeId === activeBrain;
  return {
    emissiveIntensity: active ? ACTIVE_EMISSIVE : NODE_EMISSIVE,
    opacity: 1,
    ghosted: false,
    color,
  };
}
