import { useBrain } from "../store";
import { postGameMode, postKill } from "../api/client";
import { ROUTER_LABEL } from "../constants";
import type { Stats } from "../types";

/**
 * Header — parity with the classic top bar: model badge, always-visible router
 * dot, CPU/RAM/VRAM gauges, session/today token totals, game-mode toggle, kill
 * switch, memory button — plus a state chip driven by the synthesized /ws/state
 * pipeline (new in v3).
 */
export default function Header() {
  const stats = useBrain((s) => s.stats);
  const pipeline = useBrain((s) => s.pipeline);
  const router = useBrain((s) => s.router);
  const gameMode = useBrain((s) => s.gameMode);

  const toggleGame = async () => {
    const next = !gameMode;
    useBrain.getState().setGameMode(next); // optimistic
    await postGameMode(next).catch(() => {});
  };

  return (
    <header className="topbar">
      <span className="brand">Baby</span>
      <span className="brand-sub">The Brain</span>
      {stats?.model && <span className="badge model">{stats.model}</span>}

      <span className={`router-state ${router}`} title="cloud router state">
        <span className="dot" />
        <span>{ROUTER_LABEL[router] ?? router}</span>
      </span>

      <span className={`state-chip ${pipeline}`}>{pipeline}</span>

      <div className="gauges">
        <Gauge label="cpu" percent={stats?.cpu_percent} />
        <Gauge
          label="ram"
          used={stats?.ram?.used_gb}
          total={stats?.ram?.total_gb}
          percent={stats?.ram?.percent}
        />
        <Gauge
          label="vram"
          used={stats?.gpu?.vram_used_gb}
          total={stats?.gpu?.vram_total_gb}
          na={!stats?.gpu}
        />
      </div>

      <TokensBadge stats={stats} />

      <button
        className={"game-btn" + (gameMode ? " on" : "")}
        onClick={toggleGame}
        title={
          gameMode ? "game mode on (all turns cloud)" : "toggle game mode"
        }
      >
        {gameMode ? "🎮 on" : "🎮"}
      </button>
      <button className="mem-btn" onClick={() => useBrain.getState().openMemory()}>
        🧠
      </button>
      <button className="kill-btn" onClick={() => postKill().catch(() => {})}>
        ■ Stop
      </button>
      <a className="classic-link" href="/classic">
        classic&nbsp;UI
      </a>
    </header>
  );
}

function Gauge({
  label,
  used,
  total,
  percent,
  na,
}: {
  label: string;
  used?: number;
  total?: number;
  percent?: number;
  na?: boolean;
}) {
  const pct = percent ?? (total ? ((used ?? 0) / total) * 100 : 0);
  const color =
    pct > 85 ? "var(--red)" : pct > 65 ? "var(--amber)" : "var(--accent)";
  const value = na
    ? "n/a"
    : total
      ? `${(used ?? 0).toFixed(1)}/${total.toFixed(0)}G`
      : `${Math.round(pct)}%`;
  return (
    <span className="gauge">
      <span className="gauge-label">{label}</span>
      <span className="bar">
        <span
          className="bar-fill"
          style={{ width: `${Math.min(100, pct)}%`, background: color }}
        />
      </span>
      <em>{value}</em>
    </span>
  );
}

function TokensBadge({ stats }: { stats: Stats | null }) {
  const tokens = stats?.tokens;
  if (!tokens) return null;
  const sess = tokens.session;
  const today = tokens.today;
  const brains = Object.entries(today.by_brain ?? {})
    .map(([tier, n]) => `${tier}: ${n}`)
    .join(" · ");
  const title =
    `session ${sess.total} · today ${today.total} tokens` +
    (brains ? `\ntoday by brain — ${brains}` : "");
  return (
    <span className="tokens-badge" title={title}>
      ↑{sess.prompt} ↓{sess.completion}
    </span>
  );
}
