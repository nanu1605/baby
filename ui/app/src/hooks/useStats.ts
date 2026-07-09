/**
 * /stats poll (every 5s + on mount) — the header snapshot: model, gauges, token
 * totals. Router health + game mode are also refreshed here so the header stays
 * correct even if /ws/state briefly drops.
 */
import { useEffect } from "react";
import { getStats } from "../api/client";
import { useBrain } from "../store";
import { normRouter } from "../constants";
import { effectiveCeiling } from "../graph/governor/tierMachine";

export function useStats(): void {
  useEffect(() => {
    let alive = true;
    const poll = async () => {
      try {
        const s = await getStats();
        if (!alive) return;
        const b = useBrain.getState();
        b.setStats(s);
        if (s.router?.state) b.setRouter(normRouter(s.router.state));
        if (typeof s.game_mode === "boolean") b.setGameMode(s.game_mode);
        if (s.render?.target_fps && s.render.target_fps > 0) {
          b.setTargetFps(s.render.target_fps);
        }
        // ui.brain:2d forces the 2D floor; else render.tier caps the sphere quality.
        b.setRenderCeiling(effectiveCeiling(s.ui?.brain, s.render?.tier));
      } catch {
        /* server briefly away — reconnect/next tick handles it */
      }
    };
    poll();
    const id = setInterval(poll, 5000);
    return () => {
      alive = false;
      clearInterval(id);
    };
  }, []);
}
