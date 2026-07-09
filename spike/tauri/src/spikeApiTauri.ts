// V0 shell spike — Tauri-side glue for the shared window.spikeAPI seam. Imported
// for its side effect (sets window.spikeAPI) BEFORE the shared app mounts. This
// is the one shell-specific file; ../../common stays byte-identical.

import { invoke } from "@tauri-apps/api/core";
import type { SpikeAPI, SpikeResult } from "@baby/spike-common/spikeApi";

const api: SpikeAPI = {
  coldStartShellMs: () => invoke<number | null>("cold_start_shell_ms"),
  saveResult: (result: SpikeResult) => invoke<void>("save_result", { result }),
  async saveScreenshot() {
    // Tauri v2 has no simple in-process webview capture and the R3F canvas is
    // not preserveDrawingBuffer, so the bloom verdict is a manual eyeball of the
    // live window (Win+Shift+S). The HUD + result.json carry the numbers.
    // eslint-disable-next-line no-console
    console.log("[spike] Tauri: take a manual screenshot for the bloom verdict.");
  },
};

window.spikeAPI = api;
