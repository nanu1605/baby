/**
 * One palette (V3b): the sphere reads the SAME CSS custom properties the 2D graph
 * uses (tokens.css), so there is a single source of truth for colour. Cached
 * THREE.Color per var (getComputedStyle is not free).
 */
import { Color } from "three";

const _cache = new Map<string, Color>();

export function cssColor(varName: string, fallback = "#6ea8fe"): Color {
  const hit = _cache.get(varName);
  if (hit) return hit;
  let raw = fallback;
  try {
    const v = getComputedStyle(document.documentElement).getPropertyValue(varName).trim();
    if (v) raw = v;
  } catch {
    /* no DOM (tests) → fallback */
  }
  const c = new Color(raw);
  _cache.set(varName, c);
  return c;
}

/** Node-type → CSS var (mirrors BrainGraph's NODE_VAR). */
export const NODE_VAR: Record<string, string> = {
  core: "--node-core",
  brain: "--node-brain",
  tool: "--node-tool",
  memory: "--node-memory",
  voice: "--node-voice",
  safety: "--node-safety",
  router: "--node-router",
  infra: "--node-infra",
};

export const PULSE_VAR: Record<string, string> = {
  normal: "--pulse-normal",
  confirm: "--pulse-confirm",
  error: "--pulse-error",
};

export const STATE_VAR: Record<string, string> = {
  idle: "--state-idle",
  listening: "--state-listening",
  thinking: "--state-thinking",
  speaking: "--state-speaking",
  executing: "--state-executing",
};

export function nodeColor(type: string): Color {
  return cssColor(NODE_VAR[type] ?? "--node-core");
}
