import { describe, expect, it } from "vitest";
import { regionDir, sphereAnchors, type Vec3 } from "./sphereGeometry";

const R = 3;
const NODES = [
  { id: "baby_core", group: "core" },
  { id: "router", group: "core" },
  { id: "safety_gate", group: "core" },
  { id: "voice_wake", group: "voice" },
  { id: "voice_stt", group: "voice" },
  { id: "brain:cloud", group: "brains" },
  { id: "brain:daily", group: "brains" },
  { id: "tool:a", group: "tools" },
  { id: "tool:b", group: "tools" },
  { id: "mem_facts", group: "memory" },
  { id: "scheduler", group: "infra" },
  { id: "mystery", group: "weird" }, // unknown group → exercises the -Z antipodal path
];

function normDot(a: Vec3, b: Vec3): number {
  const la = Math.hypot(...a) || 1;
  const lb = Math.hypot(...b) || 1;
  return (a[0] * b[0] + a[1] * b[1] + a[2] * b[2]) / (la * lb);
}

describe("sphereAnchors", () => {
  const pos = sphereAnchors(NODES, R);

  it("places every node with finite coords", () => {
    for (const n of NODES) {
      const p = pos.get(n.id)!;
      expect(p.every(Number.isFinite)).toBe(true);
    }
  });

  it("baby_core is the origin; router/gate flank it on the inner ±Y shell", () => {
    expect(pos.get("baby_core")).toEqual([0, 0, 0]);
    expect(pos.get("router")![1]).toBeGreaterThan(0);
    expect(pos.get("safety_gate")![1]).toBeLessThan(0);
  });

  it("echoes the 2D geography: voice west (x<0), brains east (x>0), memory south (y<0), infra north (y>0)", () => {
    expect(pos.get("voice_wake")![0]).toBeLessThan(0);
    expect(pos.get("voice_stt")![0]).toBeLessThan(0);
    expect(pos.get("brain:cloud")![0]).toBeGreaterThan(0);
    expect(pos.get("mem_facts")![1]).toBeLessThan(0);
    expect(pos.get("scheduler")![1]).toBeGreaterThan(0);
  });

  it("surface nodes sit on the sphere of radius R", () => {
    for (const id of ["voice_wake", "brain:cloud", "tool:a", "mem_facts", "scheduler"]) {
      expect(Math.hypot(...pos.get(id)!)).toBeCloseTo(R, 5);
    }
  });

  it("keeps groups in disjoint regions (voice vs brains well separated)", () => {
    expect(normDot(pos.get("voice_wake")!, pos.get("brain:cloud")!)).toBeLessThan(0.5);
  });

  it("is deterministic across calls (no RNG)", () => {
    const again = sphereAnchors(NODES, R);
    for (const n of NODES) {
      expect(again.get(n.id)).toEqual(pos.get(n.id));
    }
  });

  it("each surface node stays within its OWN group's cap of the region direction", () => {
    const CAP_DEG: Record<string, number> = {
      voice: 32,
      brains: 26,
      tools: 30,
      memory: 34,
      infra: 32,
    };
    for (const id of [
      "voice_wake",
      "voice_stt",
      "brain:cloud",
      "brain:daily",
      "tool:a",
      "tool:b",
      "mem_facts",
      "scheduler",
    ]) {
      const group = NODES.find((n) => n.id === id)!.group as
        | "voice"
        | "brains"
        | "tools"
        | "memory"
        | "infra";
      const d = regionDir(group)!;
      const capCos = Math.cos((CAP_DEG[group] * Math.PI) / 180);
      expect(normDot(pos.get(id)!, d)).toBeGreaterThanOrEqual(capCos - 1e-6);
    }
  });

  it("places an unknown group on the -Z cap (rotateFromZ antipodal branch)", () => {
    const p = pos.get("mystery")!;
    expect(p.every(Number.isFinite)).toBe(true);
    expect(p[2]).toBeLessThan(0); // pushed to the -Z pole, not dropped
    expect(Math.hypot(...p)).toBeCloseTo(R, 5);
  });
});
