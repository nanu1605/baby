import { describe, expect, it } from "vitest";
import { groupAnchors } from "./layout";

const NODES = [
  { id: "baby_core", group: "core" },
  { id: "router", group: "core" },
  { id: "safety_gate", group: "core" },
  { id: "voice_wake", group: "voice" },
  { id: "voice_vad", group: "voice" },
  { id: "brain:daily", group: "brains" },
  { id: "brain:nim_primary", group: "brains" },
  { id: "mem_facts", group: "memory" },
  { id: "task_queue", group: "infra" },
  { id: "scheduler", group: "infra" },
  { id: "tool:a", group: "tools" },
  { id: "tool:b", group: "tools" },
  { id: "tool:c", group: "tools" },
];

describe("groupAnchors", () => {
  it("pins every node to a finite fx/fy", () => {
    const a = groupAnchors(NODES);
    expect(a.size).toBe(NODES.length);
    for (const n of NODES) {
      const p = a.get(n.id)!;
      expect(Number.isFinite(p.fx)).toBe(true);
      expect(Number.isFinite(p.fy)).toBe(true);
    }
  });

  it("places baby_core dead center", () => {
    const a = groupAnchors(NODES);
    expect(a.get("baby_core")).toEqual({ fx: 0, fy: 0 });
  });

  it("is deterministic across calls", () => {
    const a = groupAnchors(NODES);
    const b = groupAnchors(NODES);
    for (const n of NODES) {
      expect(a.get(n.id)).toEqual(b.get(n.id));
    }
  });

  it("keeps group regions disjoint (columns + bands separate)", () => {
    const a = groupAnchors(NODES);
    const fx = (id: string) => a.get(id)!.fx;
    const fy = (id: string) => a.get(id)!.fy;

    // west → east column order: voice < core < brains < tools
    expect(fx("voice_wake")).toBeLessThan(fx("baby_core"));
    expect(fx("baby_core")).toBeLessThan(fx("brain:daily"));
    expect(fx("brain:daily")).toBeLessThan(fx("tool:a"));

    // north/south bands clear the center column's vertical span (|y| <= 110)
    expect(fy("task_queue")).toBeLessThan(-110); // infra north
    expect(fy("mem_facts")).toBeGreaterThan(110); // memory south

    // every tool sits east of every non-tool
    const toolFx = ["tool:a", "tool:b", "tool:c"].map(fx);
    const others = ["voice_wake", "baby_core", "brain:daily"].map(fx);
    expect(Math.min(...toolFx)).toBeGreaterThan(Math.max(...others));
  });
});
