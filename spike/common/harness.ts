// V0 shell spike — the self-measuring harness. Byte-identical across shells.
//
// Collects a fixed-window frame-time trace, polls the real backend GET /stats for
// VRAM, computes fps p50 + 1%-low + avg, then hands a SpikeResult to the shell's
// spikeAPI (which persists it + a screenshot). The owner runs each shell on the
// 5060 Ti and pastes the two result.json files back.

import { getSpikeAPI, type SpikeResult } from "./spikeApi";

export const WINDOW_S = 60; // fixed measurement window
const STATS_URL = "http://127.0.0.1:8765/stats"; // the existing backend endpoint
const VRAM_POLL_MS = 1000;

interface VramSample {
  used: number;
  total: number;
  util: number;
  name: string;
}

export class SpikeHarness {
  private readonly shell: SpikeResult["shell"];
  private frameTimesMs: number[] = [];
  private started = false;
  private finished = false;
  private startMs = 0;
  private firstFrameMs = 0;
  private vram: VramSample[] = [];
  private vramTimer: ReturnType<typeof setInterval> | null = null;
  private onDone: (r: SpikeResult) => void;

  constructor(shell: SpikeResult["shell"], onDone: (r: SpikeResult) => void) {
    this.shell = shell;
    this.onDone = onDone;
  }

  /** Call once, when the scene first mounts. Starts the VRAM poll. */
  start(): void {
    if (this.started) return;
    this.started = true;
    this.startMs = performance.now();
    this.pollVram(); // immediate baseline sample
    this.vramTimer = setInterval(() => this.pollVram(), VRAM_POLL_MS);
  }

  /** Call every rendered frame with the frame delta in seconds (R3F useFrame delta). */
  frame(deltaSeconds: number): void {
    if (!this.started || this.finished) return;
    const now = performance.now();
    if (this.firstFrameMs === 0) {
      // navigationStart is timeOrigin; first painted 3D frame is now.
      this.firstFrameMs = now;
    }
    // Ignore absurd deltas (tab throttle / first frame) so they don't skew p50.
    const ms = deltaSeconds * 1000;
    if (ms > 0 && ms < 1000) this.frameTimesMs.push(ms);

    if (now - this.startMs >= WINDOW_S * 1000) {
      void this.finish();
    }
  }

  private async pollVram(): Promise<void> {
    try {
      const res = await fetch(STATS_URL, { cache: "no-store" });
      if (!res.ok) return;
      const j = await res.json();
      const g = j?.gpu;
      if (g && typeof g.vram_used_gb === "number") {
        this.vram.push({
          used: g.vram_used_gb,
          total: g.vram_total_gb ?? 0,
          util: g.util_percent ?? 0,
          name: g.name ?? "unknown",
        });
      }
    } catch {
      // Backend not up / CORS blocked → VRAM comes from nvidia-smi per the README.
    }
  }

  private async finish(): Promise<void> {
    if (this.finished) return;
    this.finished = true;
    if (this.vramTimer) clearInterval(this.vramTimer);

    const fps = this.frameTimesMs.map((ms) => 1000 / ms).filter((f) => isFinite(f));
    const api = getSpikeAPI();
    const coldShell = await api.coldStartShellMs();

    const result: SpikeResult = {
      shell: this.shell,
      ok: fps.length > 0,
      measured_at: new Date().toISOString(),
      window_s: WINDOW_S,
      fps_p50: percentileFps(fps, 50),
      fps_1pct_low: onePercentLow(this.frameTimesMs),
      fps_avg: fps.length ? fps.reduce((a, b) => a + b, 0) / fps.length : 0,
      frame_count: fps.length,
      cold_start_render_ms: Math.round(this.firstFrameMs), // timeOrigin -> first frame
      cold_start_shell_ms: coldShell,
      vram_used_gb: last(this.vram)?.used ?? null,
      vram_total_gb: last(this.vram)?.total ?? null,
      vram_used_gb_min: this.vram.length ? Math.min(...this.vram.map((v) => v.used)) : null,
      vram_used_gb_max: this.vram.length ? Math.max(...this.vram.map((v) => v.used)) : null,
      gpu_util_max: this.vram.length ? Math.max(...this.vram.map((v) => v.util)) : null,
      gpu_name: last(this.vram)?.name ?? null,
      vram_samples: this.vram.length,
      notes:
        this.vram.length === 0
          ? "No /stats VRAM samples — start the backend (uv run python run.py --ui) and/or read VRAM from nvidia-smi (see README)."
          : "",
    };

    await api.saveScreenshot();
    await api.saveResult(result);
    this.onDone(result);
  }

  isFinished(): boolean {
    return this.finished;
  }
}

function last<T>(arr: T[]): T | undefined {
  return arr.length ? arr[arr.length - 1] : undefined;
}

/** p-th percentile of an fps array (p50 = median). */
function percentileFps(fps: number[], p: number): number {
  if (!fps.length) return 0;
  const sorted = [...fps].sort((a, b) => a - b);
  const idx = Math.min(sorted.length - 1, Math.floor((p / 100) * sorted.length));
  return round1(sorted[idx]);
}

/** 1% low = average fps of the worst 1% of frames (slowest frame times). */
function onePercentLow(frameTimesMs: number[]): number {
  if (!frameTimesMs.length) return 0;
  const slowest = [...frameTimesMs].sort((a, b) => b - a); // slow first
  const cut = Math.max(1, Math.ceil(slowest.length * 0.01));
  const worst = slowest.slice(0, cut);
  const avgMs = worst.reduce((a, b) => a + b, 0) / worst.length;
  return round1(1000 / avgMs);
}

function round1(n: number): number {
  return Math.round(n * 10) / 10;
}
