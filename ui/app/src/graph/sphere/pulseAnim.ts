/**
 * Pulse animation math (V3d) — the pure, framework-free core the 3D `Pulses.tsx`
 * layer sits on. Kept out of the R3F component so it unit-tests directly (mirrors
 * greatCircle.ts / sphereGeometry.ts): sampling an arc polyline at a parameter,
 * per-edge coalescing, a mesh free-list pool, and the pulse/flash life envelopes.
 *
 * No three / no React import — plain tuple + number math.
 */
import type { Vec3 } from "./sphereGeometry";

/** Traveling-pulse lifetime — one arc traversal (~500 ms, matches the 2D sprite). */
export const PULSE_MS = 500;
/** Node-flash lifetime (~600 ms, matches BrainGraph's flash window). */
export const FLASH_MS = 600;

/**
 * Point on an arc polyline (arcPoints output) at parameter t∈[0,1]. idx = t·(n-1);
 * lerp the two bracketing samples. Clamps t and returns the exact endpoints at 0/1
 * so a pulse starts on `from` and lands on `to`.
 */
export function samplePolyline(points: Vec3[], t: number): Vec3 {
  const n = points.length;
  if (n === 0) return [0, 0, 0];
  if (n === 1) return points[0];
  const tc = t <= 0 ? 0 : t >= 1 ? 1 : t;
  const f = tc * (n - 1);
  const i0 = Math.floor(f);
  if (i0 >= n - 1) return points[n - 1];
  const a = points[i0];
  const b = points[i0 + 1];
  const frac = f - i0;
  return [
    a[0] + (b[0] - a[0]) * frac,
    a[1] + (b[1] - a[1]) * frac,
    a[2] + (b[2] - a[2]) * frac,
  ];
}

/** Per-edge rate limiter — the 3D analogue of BrainGraph's 150 ms coalesce. */
export interface Coalescer {
  /** True (and records `now`) if `key` may emit; false if inside the window. */
  allow(key: string, now: number): boolean;
}

export function makeCoalescer(windowMs = 150): Coalescer {
  const last = new Map<string, number>();
  return {
    allow(key, now) {
      const prev = last.get(key);
      if (prev !== undefined && now - prev < windowMs) return false;
      last.set(key, now);
      return true;
    },
  };
}

/** A fixed-size slot pool — acquire returns a free index or null (overflow drop). */
export interface Pool {
  readonly size: number;
  acquire(): number | null;
  release(i: number): void;
}

export function makePool(size: number): Pool {
  const free: number[] = [];
  for (let i = size - 1; i >= 0; i--) free.push(i); // acquire hands out 0,1,2,…
  const used = new Set<number>();
  return {
    size,
    acquire() {
      const i = free.pop();
      if (i === undefined) return null;
      used.add(i);
      return i;
    },
    release(i) {
      if (used.delete(i)) free.push(i);
    },
  };
}

/** Traveling-pulse progress over its life; `done` once the arc is fully traversed. */
export function pulseProgress(
  age: number,
  lifeS = PULSE_MS / 1000,
): { t: number; done: boolean } {
  const t = age / lifeS;
  return t >= 1 ? { t: 1, done: true } : { t, done: false };
}

/**
 * Node-flash ring envelope over its life: scale grows outward from the node, opacity
 * fades to zero. `done` at end of life so the runner can release the slot.
 */
export function flashEnvelope(
  age: number,
  lifeS = FLASH_MS / 1000,
): { scale: number; opacity: number; done: boolean } {
  const t = age / lifeS;
  if (t >= 1) return { scale: 1 + 1.6, opacity: 0, done: true };
  return { scale: 1 + 1.6 * t, opacity: 1 - t, done: false };
}
