/**
 * Per-node visual targets (V3c, pulled forward from V3e's recolor/ghost scope):
 * honest-data — a node's glow must reflect its REAL state. Game mode offloads the
 * local 9B, so `brain:daily` ghosts: emissive drops below the bloom threshold (the
 * glow washes out) and the body fades, mirroring the 2D graph's dashed ghost
 * (BrainGraph drawNode). Pure — vitest-tested, no three import.
 */

/** Resting emissive for a loaded node — HDR punch above the bloom threshold. */
export const NODE_EMISSIVE = 1.35;
/** Ghost emissive — far below luminanceThreshold 0.55 so bloom fully washes out. */
export const GHOST_EMISSIVE = 0.06;
/** Ghost body opacity — echoes the 2D graph's 0.28 ghost alpha. */
export const GHOST_OPACITY = 0.3;

export interface NodeVisualTarget {
  emissiveIntensity: number;
  opacity: number;
  ghosted: boolean;
}

/**
 * Visual target for a node given the live signals. Only the local brain ghosts on
 * game mode today (the honest "it is offloaded" state); router-health recolor and
 * active-brain highlight join in V3e.
 */
export function nodeVisualTarget(nodeId: string, gameMode: boolean): NodeVisualTarget {
  if (gameMode && nodeId === "brain:daily") {
    return { emissiveIntensity: GHOST_EMISSIVE, opacity: GHOST_OPACITY, ghosted: true };
  }
  return { emissiveIntensity: NODE_EMISSIVE, opacity: 1, ghosted: false };
}
