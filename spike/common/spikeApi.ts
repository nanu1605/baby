// V0 shell spike — the ONE seam that differs between shells.
//
// The scene + sampler + camera path + result shape are byte-identical across
// Tauri and Electron. The only shell-specific glue is *how* a result.json and a
// screenshot get written to disk, and *when* the shell process started (for the
// shell-side cold-start number). Each shell injects a `window.spikeAPI`
// implementing this interface; the shared harness only ever calls through it.

export interface SpikeResult {
  shell: "tauri" | "electron" | "browser";
  ok: boolean;
  measured_at: string; // ISO, stamped by the shell (renderer clock is fine for a spike)
  window_s: number; // measurement window length
  fps_p50: number;
  fps_1pct_low: number;
  fps_avg: number;
  frame_count: number;
  cold_start_render_ms: number; // navigationStart -> first 3D frame
  cold_start_shell_ms: number | null; // process spawn -> window shown (shell-side)
  vram_used_gb: number | null; // last /stats sample
  vram_total_gb: number | null;
  vram_used_gb_min: number | null; // over the window
  vram_used_gb_max: number | null;
  gpu_util_max: number | null;
  gpu_name: string | null;
  vram_samples: number; // how many /stats polls succeeded
  notes: string;
}

export interface SpikeAPI {
  // Shell-side process-start -> first-window timestamp, in ms. null if the shell
  // can't provide it (browser fallback).
  coldStartShellMs(): Promise<number | null>;
  // Persist the finished result next to the shell (spike/<shell>/result.json).
  saveResult(result: SpikeResult): Promise<void>;
  // Capture a screenshot of the rendered scene (spike/<shell>/screenshot.png) so
  // the owner can eyeball bloom acceptability — the one inherently-human metric.
  saveScreenshot(): Promise<void>;
}

declare global {
  interface Window {
    spikeAPI?: SpikeAPI;
  }
}

// Browser fallback so the shared scene can also be opened in a plain `vite dev`
// for quick visual iteration (no disk write, no shell cold-start).
export const browserFallback: SpikeAPI = {
  async coldStartShellMs() {
    return null;
  },
  async saveResult(result: SpikeResult) {
    // eslint-disable-next-line no-console
    console.log("[spike] result (browser fallback, not persisted):", result);
  },
  async saveScreenshot() {
    // eslint-disable-next-line no-console
    console.log("[spike] screenshot skipped (browser fallback)");
  },
};

export function getSpikeAPI(): SpikeAPI {
  return window.spikeAPI ?? browserFallback;
}
