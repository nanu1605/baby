import { describe, it, expect, beforeEach } from "vitest";
import {
  foldAmplitude,
  micLevel,
  ttsLevel,
  _resetAmplitude,
} from "./amplitude";
import type { WSFrame } from "../types";

const frame = (type: string, extra: Record<string, unknown> = {}): WSFrame =>
  ({ type, ...extra }) as WSFrame;

describe("foldAmplitude", () => {
  beforeEach(() => _resetAmplitude());

  it("consumes mic_rms and stores the level", () => {
    expect(foldAmplitude(frame("mic_rms", { rms: 0.6 }), 1000)).toBe(true);
    expect(micLevel(1000)).toBeCloseTo(0.6, 5);
  });

  it("consumes tts_rms independently of mic", () => {
    foldAmplitude(frame("mic_rms", { rms: 0.3 }), 1000);
    expect(foldAmplitude(frame("tts_rms", { rms: 0.8 }), 1000)).toBe(true);
    expect(ttsLevel(1000)).toBeCloseTo(0.8, 5);
    expect(micLevel(1000)).toBeCloseTo(0.3, 5);
  });

  it("does NOT consume other frame kinds (they must reach pushEvent)", () => {
    for (const t of ["tool_start", "status", "turn_end", "token"]) {
      expect(foldAmplitude(frame(t), 1000)).toBe(false);
    }
  });

  it("clamps out-of-range / missing rms to [0,1]", () => {
    foldAmplitude(frame("mic_rms", { rms: 9 }), 1000);
    expect(micLevel(1000)).toBe(1);
    foldAmplitude(frame("mic_rms", { rms: -2 }), 1000);
    expect(micLevel(1000)).toBe(0);
    foldAmplitude(frame("tts_rms", {}), 1000); // missing rms → 0
    expect(ttsLevel(1000)).toBe(0);
  });
});

describe("amplitude decay", () => {
  beforeEach(() => _resetAmplitude());

  it("holds the value at the update instant", () => {
    foldAmplitude(frame("mic_rms", { rms: 1 }), 5000);
    expect(micLevel(5000)).toBe(1);
  });

  it("decays toward 0 as time passes with no new frame", () => {
    foldAmplitude(frame("tts_rms", { rms: 1 }), 0);
    const early = ttsLevel(100);
    const later = ttsLevel(600);
    expect(early).toBeLessThan(1);
    expect(later).toBeLessThan(early);
    expect(later).toBeGreaterThanOrEqual(0);
    expect(later).toBeLessThan(0.1); // well relaxed after ~0.6s (τ=220ms)
  });

  it("a fresh frame resets the decay clock", () => {
    foldAmplitude(frame("mic_rms", { rms: 0.5 }), 0);
    expect(micLevel(1000)).toBeLessThan(0.05); // decayed
    foldAmplitude(frame("mic_rms", { rms: 0.5 }), 1000);
    expect(micLevel(1000)).toBeCloseTo(0.5, 5); // reset
  });
});
