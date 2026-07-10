/**
 * Honest amplitude channel (V3e). `mic_rms` / `tts_rms` arrive on /ws/activity at
 * ~15 / ~10 Hz — far too fast for zustand (a `set()` re-renders every subscriber),
 * so they live in a module-level mutable read transiently inside `useFrame` (the
 * `pulseBus` precedent, decoupled from the React store).
 *
 * `foldAmplitude` intercepts these frames in `useActivitySocket` BEFORE `pushEvent`
 * so a 15 Hz stream never floods the 500-cap event ring. The getters apply an
 * exponential decay from the last update, so a level relaxes to 0 once frames stop
 * (speaking ends → the shimmer settles) rather than freezing at the last value.
 */
import type { WSFrame } from "../types";

/** Time-constant of the relax-to-zero decay when frames stop arriving (ms). */
const DECAY_MS = 220;

interface Level {
  v: number;
  t: number;
}
const mic: Level = { v: 0, t: 0 };
const tts: Level = { v: 0, t: 0 };

function nowMs(): number {
  return typeof performance !== "undefined" ? performance.now() : 0;
}
function clamp01(x: number): number {
  return x < 0 ? 0 : x > 1 ? 1 : x;
}

/**
 * Route a `mic_rms` / `tts_rms` frame into the amplitude refs. Returns `true` when
 * the frame was consumed — the caller MUST then early-return, before `pushEvent`.
 */
export function foldAmplitude(msg: WSFrame, now: number = nowMs()): boolean {
  if (msg.type === "mic_rms") {
    mic.v = clamp01(typeof msg.rms === "number" ? msg.rms : 0);
    mic.t = now;
    return true;
  }
  if (msg.type === "tts_rms") {
    tts.v = clamp01(typeof msg.rms === "number" ? msg.rms : 0);
    tts.t = now;
    return true;
  }
  return false;
}

function decayed(l: Level, now: number): number {
  const dt = now - l.t;
  if (dt <= 0) return l.v;
  return l.v * Math.exp(-dt / DECAY_MS);
}

/** Current mic loudness 0..1 (decays to 0 when `mic_rms` frames stop). */
export function micLevel(now: number = nowMs()): number {
  return decayed(mic, now);
}
/** Current TTS loudness 0..1 (decays to 0 when `tts_rms` frames stop). */
export function ttsLevel(now: number = nowMs()): number {
  return decayed(tts, now);
}

/** Test-only: clear both levels. */
export function _resetAmplitude(): void {
  mic.v = 0;
  mic.t = 0;
  tts.v = 0;
  tts.t = 0;
}
