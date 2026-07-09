/**
 * Sphere geometry (V3b) — the 3D analogue of layout.ts `groupAnchors`. Each fixed
 * node GROUP owns a spherical region (a direction + a cap half-angle) that echoes the
 * 2D geography (voice west, brains equator-east, tools east, memory south, infra
 * north, core center); nodes fill their cap deterministically via a golden-angle
 * Fibonacci patch, so the layout is stable across reloads (honest, non-random).
 *
 * Pure vector math on plain [x,y,z] tuples — no three / no GL — so it unit-tests
 * directly (mirrors layout.test.ts). The render boundary converts to THREE.Vector3.
 */

export type Vec3 = [number, number, number];
export type SphereGroup = "core" | "voice" | "brains" | "tools" | "memory" | "infra";

interface Region {
  /** Region center direction (unit-ish; normalized internally). */
  dir: Vec3;
  /** Cap half-angle in degrees the group's nodes spread across. */
  halfAngleDeg: number;
}

/** Region table — echoes the 2D W→E / N–S semantics (layout.ts). `core` is special. */
const REGIONS: Record<Exclude<SphereGroup, "core">, Region> = {
  voice: { dir: [-1, 0, 0], halfAngleDeg: 32 }, // west
  brains: { dir: [0.85, 0, 0.35], halfAngleDeg: 26 }, // center-east
  tools: { dir: [0.7, 0, -0.6], halfAngleDeg: 30 }, // far-east
  memory: { dir: [0, -1, 0], halfAngleDeg: 34 }, // south
  infra: { dir: [0, 1, 0], halfAngleDeg: 32 }, // north
};

const GOLDEN_ANGLE = Math.PI * (3 - Math.sqrt(5)); // ~2.399963 rad

// --- tiny vector helpers (tuple math) ---------------------------------------
function len(v: Vec3): number {
  return Math.hypot(v[0], v[1], v[2]);
}
function norm(v: Vec3): Vec3 {
  const l = len(v) || 1;
  return [v[0] / l, v[1] / l, v[2] / l];
}
function dot(a: Vec3, b: Vec3): number {
  return a[0] * b[0] + a[1] * b[1] + a[2] * b[2];
}
function cross(a: Vec3, b: Vec3): Vec3 {
  return [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]];
}
function scale(v: Vec3, s: number): Vec3 {
  return [v[0] * s, v[1] * s, v[2] * s];
}
function add(a: Vec3, b: Vec3): Vec3 {
  return [a[0] + b[0], a[1] + b[1], a[2] + b[2]];
}

/** Rotate a vector defined around +Z so that +Z maps onto `dir` (Rodrigues). */
function rotateFromZ(v: Vec3, dir: Vec3): Vec3 {
  const d = norm(dir);
  const z: Vec3 = [0, 0, 1];
  const c = dot(z, d);
  if (c > 0.999999) return v; // already +Z
  if (c < -0.999999) return [v[0], -v[1], -v[2]]; // antipodal: 180° about X
  const axis = norm(cross(z, d));
  const angle = Math.acos(Math.max(-1, Math.min(1, c)));
  const s = Math.sin(angle);
  const co = Math.cos(angle);
  // v_rot = v*cos + (axis×v)*sin + axis*(axis·v)*(1-cos)
  const term1 = scale(v, co);
  const term2 = scale(cross(axis, v), s);
  const term3 = scale(axis, dot(axis, v) * (1 - co));
  return add(add(term1, term2), term3);
}

/** i-th of `count` points in a Fibonacci cap of half-angle α (radians) around +Z. */
function fibCapPoint(i: number, count: number, alpha: number): Vec3 {
  const zMin = Math.cos(alpha);
  const t = count > 1 ? i / (count - 1) : 0; // 0 = center pole, 1 = cap edge
  const z = 1 - t * (1 - zMin);
  const r = Math.sqrt(Math.max(0, 1 - z * z));
  const az = i * GOLDEN_ANGLE;
  return [r * Math.cos(az), r * Math.sin(az), z];
}

/**
 * Deterministic sphere positions for the fixed topology, radius R. Mirrors
 * `groupAnchors`: group nodes in input order, place each in its region's cap.
 * `baby_core` sits at the origin; `router`/`safety_gate` on the inner ±Y shell
 * (echoing the 2D router-north / gate-south column).
 */
export function sphereAnchors(
  nodes: { id: string; group: string }[],
  R = 3,
): Map<string, Vec3> {
  const out = new Map<string, Vec3>();
  const buckets = new Map<string, { id: string; group: string }[]>();
  for (const n of nodes) {
    if (n.group === "core") continue; // core handled explicitly below
    const arr = buckets.get(n.group) ?? [];
    arr.push(n);
    buckets.set(n.group, arr);
  }
  for (const [group, arr] of buckets) {
    const region = REGIONS[group as Exclude<SphereGroup, "core">];
    if (!region) {
      // Unknown group → a small polar cap on -Z so it is still placed, not dropped.
      arr.forEach((n, i) => {
        const p = fibCapPoint(i, arr.length, (30 * Math.PI) / 180);
        out.set(n.id, scale(rotateFromZ(p, [0, 0, -1]), R));
      });
      continue;
    }
    const alpha = (region.halfAngleDeg * Math.PI) / 180;
    arr.forEach((n, i) => {
      const local = fibCapPoint(i, arr.length, alpha);
      out.set(n.id, scale(rotateFromZ(local, region.dir), R));
    });
  }
  // Core column (radially inside the shell so it never collides with infra/memory).
  for (const n of nodes) {
    if (n.group !== "core") continue;
    if (n.id === "baby_core") out.set(n.id, [0, 0, 0]);
    else if (n.id === "router") out.set(n.id, [0, 0.28 * R, 0]);
    else if (n.id === "safety_gate") out.set(n.id, [0, -0.28 * R, 0]);
    else out.set(n.id, [0, 0, 0.28 * R]); // any extra core node on the inner +Z
  }
  return out;
}

/** Exposed for tests: the region direction for a group (normalized). */
export function regionDir(group: SphereGroup): Vec3 | null {
  if (group === "core") return [0, 0, 0];
  const r = REGIONS[group];
  return r ? norm(r.dir) : null;
}
