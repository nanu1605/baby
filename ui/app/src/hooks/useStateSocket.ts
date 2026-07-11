/**
 * /ws/state — the synthesized pipeline gauge (B1). Drives the header state chip
 * and (in B3) the central core-node gauge. Also carries router health + game
 * mode, kept in sync with the /stats poll.
 */
import { useEffect } from "react";
import { openSocket } from "../api/socket";
import { useBrain } from "../store";
import { normRouter } from "../constants";
import type { PipelineState } from "../types";

const PIPELINE = new Set<PipelineState>([
  "idle",
  "listening",
  "thinking",
  "speaking",
  "executing",
]);

export function useStateSocket(): void {
  useEffect(() => {
    const sock = openSocket("/ws/state", (msg) => {
      if (msg.type !== "state") return;
      const b = useBrain.getState();
      if (typeof msg.state === "string" && PIPELINE.has(msg.state as PipelineState)) {
        b.setPipeline(msg.state as PipelineState);
      }
      if (typeof msg.router === "string") b.setRouter(normRouter(msg.router));
      if (typeof msg.game_mode === "boolean") b.setGameMode(msg.game_mode);
      // V2: the additive throttled VRAM signal (observability; the watchdog now
      // keys on model residency below).
      if (typeof msg.vram_used_gb === "number" && typeof msg.vram_total_gb === "number") {
        b.setVram({ usedGb: msg.vram_used_gb, totalGb: msg.vram_total_gb });
      }
      // V3 watchdog: local model resident → the governor sheds bloom (lite3d cap).
      if (typeof msg.local_model_loaded === "boolean") {
        b.setLocalModelLoaded(msg.local_model_loaded);
      }
    }, (up) => useBrain.getState().setWsStatus("state", up));
    return () => sock.close();
  }, []);
}
