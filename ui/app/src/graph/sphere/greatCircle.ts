/**
 * Great-circle arcs (V3b) — dark edges bow along the sphere surface instead of
 * cutting through it. Pure tuple math, no three/GL, so it unit-tests directly.
 */
import type { Vec3 } from "./sphereGeometry";

function len(v: Vec3): number {
  return Math.hypot(v[0], v[1], v[2]);
}
function norm(v: Vec3): Vec3 {
  const l = len(v) || 1;
  return [v[0] / l, v[1] / l, v[2] / l];
}
function clamp(x: number, lo: number, hi: number): number {
  return x < lo ? lo : x > hi ? hi : x;
}

/** Spherical interpolation of the DIRECTIONS of a,b (returns a unit vector). */
export function slerpUnit(a: Vec3, b: Vec3, t: number): Vec3 {
  const ua = norm(a);
  const ub = norm(b);
  const omega = Math.acos(clamp(ua[0] * ub[0] + ua[1] * ub[1] + ua[2] * ub[2], -1, 1));
  if (omega < 1e-6) return ua;
  const s = Math.sin(omega);
  const w1 = Math.sin((1 - t) * omega) / s;
  const w2 = Math.sin(t * omega) / s;
  return [ua[0] * w1 + ub[0] * w2, ua[1] * w1 + ub[1] * w2, ua[2] * w1 + ub[2] * w2];
}

export interface ArcOpts {
  segments?: number;
  /** Outward bow at the arc midpoint, as a fraction of radius. */
  bulge?: number;
}

/**
 * Sample points for an edge a→b. Both on (roughly) the same sphere → a bowed
 * great-circle arc; an endpoint at the origin (baby_core) → a straight radial line.
 */
export function arcPoints(a: Vec3, b: Vec3, opts: ArcOpts = {}): Vec3[] {
  const segments = opts.segments ?? 24;
  const bulge = opts.bulge ?? 0.15;
  const ra = len(a);
  const rb = len(b);
  const pts: Vec3[] = [];

  if (ra < 1e-6 || rb < 1e-6) {
    // Radial straight line (origin-touching edge).
    for (let i = 0; i <= segments; i++) {
      const t = i / segments;
      pts.push([a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t, a[2] + (b[2] - a[2]) * t]);
    }
    return pts;
  }

  for (let i = 0; i <= segments; i++) {
    const t = i / segments;
    const u = slerpUnit(a, b, t);
    // Lerp the endpoint radii so the arc lands exactly on BOTH nodes even when they
    // sit at different radii (router/gate on the inner shell ↔ surface nodes); the
    // bulge still bows it outward at the middle (0 at the ends).
    const base = ra + (rb - ra) * t;
    const radius = base * (1 + bulge * Math.sin(Math.PI * t));
    pts.push([u[0] * radius, u[1] * radius, u[2] * radius]);
  }
  return pts;
}
