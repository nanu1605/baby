/**
 * Deterministic pinned geography for the brain graph (B2). Every node gets a
 * fixed `fx/fy` by its `group`, so the topology has stable geography — no layout
 * roulette on reload. Regions are pairwise-disjoint rectangles in a virtual
 * space centered at (0,0); react-force-graph copies fx→x during warmup and the
 * camera zoom-to-fits afterward.
 *
 *   infra   — north band (top)
 *   voice   — west column (left)
 *   core    — center column (baby_core dead center, router/gate stacked)
 *   brains  — center-east column
 *   memory  — south band (bottom)
 *   tools   — far-east grid
 *
 * Pure + deterministic → unit-tested (regions disjoint, every node pinned).
 */

export interface AnchorPoint {
  fx: number;
  fy: number;
}

interface LayoutNode {
  id: string;
  group: string;
}

/** Fixed positions for the hand-authored center column. */
const CORE_POS: Record<string, AnchorPoint> = {
  baby_core: { fx: 0, fy: 0 },
  router: { fx: 0, fy: -110 },
  safety_gate: { fx: 0, fy: 110 },
};

/** Spread `count` items evenly across [min, max]; single item sits at the mid. */
function spread(i: number, count: number, min: number, max: number): number {
  if (count <= 1) return (min + max) / 2;
  return min + ((max - min) * i) / (count - 1);
}

function place(group: string, i: number, count: number, id: string): AnchorPoint {
  switch (group) {
    case "core":
      // Known core nodes are fixed; any extra falls back to the center column.
      return CORE_POS[id] ?? { fx: 0, fy: spread(i, count, -130, 130) };
    case "infra": // north band
      return { fx: spread(i, count, -300, 300), fy: -280 };
    case "voice": // west column
      return { fx: -420, fy: spread(i, count, -160, 160) };
    case "brains": // center-east column
      return { fx: 210, fy: spread(i, count, -140, 140) };
    case "memory": // south band
      return { fx: spread(i, count, -100, 100), fy: 280 };
    case "tools": {
      // far-east grid: up to 8 rows per column, columns march east.
      const rows = 8;
      const col = Math.floor(i / rows);
      const row = i % rows;
      return { fx: 400 + col * 70, fy: -245 + row * 70 };
    }
    default:
      // Unknown group — scatter along a lane so nodes never stack on origin.
      return { fx: spread(i, count, -300, 300), fy: 380 };
  }
}

export function groupAnchors(nodes: LayoutNode[]): Map<string, AnchorPoint> {
  const byGroup = new Map<string, string[]>();
  for (const n of nodes) {
    const arr = byGroup.get(n.group);
    if (arr) arr.push(n.id);
    else byGroup.set(n.group, [n.id]);
  }

  const out = new Map<string, AnchorPoint>();
  for (const [group, ids] of byGroup) {
    ids.forEach((id, i) => out.set(id, place(group, i, ids.length, id)));
  }
  return out;
}
