/**
 * /stats poll (every 5s + on mount) — the header snapshot: model, gauges, token
 * totals. Router health + game mode are also refreshed here so the header stays
 * correct even if /ws/state briefly drops.
 */
import { useEffect } from "react";
import { getStats } from "../api/client";
import { useBrain } from "../store";
import { normRouter } from "../constants";

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
