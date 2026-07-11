/**
 * Decorative-motion level (V4). One pure decision folding the three collapse triggers —
 * OS prefers-reduced-motion, the user's performanceMode opt-in, and the governor's 2D
 * floor — into a single verdict the whole chrome reads. `useMotionFlag` publishes it to
 * `<body data-motion>`, and app.css gates transitions/keyframes off it (durations zero at
 * "off"). Pure → vitest; no React/DOM coupling.
 */
import type { Tier } from "./governor/tierMachine";

export type MotionLevel = "full" | "lite" | "off";

/**
 * full — full3d tier, motion allowed: emphasized/spring eases, the works.
 * lite — lite3d tier: keep the essential enter/exit, drop the flourish.
 * off  — reduced-motion OR performanceMode OR the 2D floor: decorative motion collapses
 *        (CSS zeroes the durations), only instant state changes remain.
 */
export function motionLevel(
  reduced: boolean,
  performanceMode: boolean,
  tier: Tier,
): MotionLevel {
  if (reduced || performanceMode || tier === "2d") return "off";
  if (tier === "lite3d") return "lite";
  return "full";
}
