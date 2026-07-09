import { useEffect, useMemo, useState } from "react";
import { useBrain } from "../store";
import { cancelTask, getNodeStats, runSchedule, setToolFlag } from "../api/client";
import { nodeEvents } from "../graph/nodeEvents";
import MemoryPanel from "./MemoryPanel";
import type { GraphNode, LiveEvent, NodeStats } from "../types";

/**
 * Node inspector drawer (B4). Click a graph node → this opens: what it is (blurb
 * from /api/graph, already in the store), live /api/nodes/{id}/stats (polled),
 * node-filtered recent events, and per-type controls. Never an empty drawer —
 * even a bare subsystem shows its blurb.
 */
export default function InspectorDrawer() {
  const selectedNode = useBrain((s) => s.selectedNode);
  const graph = useBrain((s) => s.graph);
  const events = useBrain((s) => s.events);
  const focusFact = useBrain((s) => s.focusFact);
  const [stats, setStats] = useState<NodeStats | null>(null);

  const node = useMemo(
    () => graph?.nodes.find((n) => n.id === selectedNode) ?? null,
    [graph, selectedNode],
  );

  const refresh = () => {
    if (selectedNode) getNodeStats(selectedNode).then(setStats).catch(() => {});
  };

  useEffect(() => {
    if (!selectedNode) {
      setStats(null);
      return;
    }
    let alive = true;
    const load = () =>
      getNodeStats(selectedNode)
        .then((s) => alive && setStats(s))
        .catch(() => {});
    load();
    const id = setInterval(load, 4000);
    return () => {
      alive = false;
      clearInterval(id);
    };
  }, [selectedNode]);

  if (!selectedNode) return null;
  const close = () => useBrain.getState().selectNode(null);
  const recent = nodeEvents(events, selectedNode);

  return (
    <aside className="inspector">
      <div className="insp-head">
        <div className="insp-titles">
          <div className="insp-title">{node?.label ?? selectedNode}</div>
          <div className="insp-role">{node?.role || node?.type || "subsystem"}</div>
        </div>
        <button className="insp-close" onClick={close} title="close">
          ✕
        </button>
      </div>
      {node?.blurb && <p className="insp-blurb">{node.blurb}</p>}
      <div className="insp-body">
        <Controls
          node={node}
          id={selectedNode}
          stats={stats}
          refresh={refresh}
          focusFact={focusFact}
        />
        <RecentEvents events={recent} />
      </div>
    </aside>
  );
}

function Controls({
  node,
  id,
  stats,
  refresh,
  focusFact,
}: {
  node: GraphNode | null;
  id: string;
  stats: NodeStats | null;
  refresh: () => void;
  focusFact: number | null;
}) {
  if (id.startsWith("tool:")) return <ToolControls id={id} stats={stats} refresh={refresh} />;
  if (id.startsWith("brain:")) return <BrainControls id={id} stats={stats} />;
  if (id === "safety_gate") return <GatePanel />;
  if (id === "task_queue") return <TaskQueuePanel stats={stats} refresh={refresh} />;
  if (id === "scheduler") return <SchedulerPanel stats={stats} />;
  if (id === "mem_facts" || id === "mem_rag" || id === "mem_summaries")
    // Only facts anchor to mem_facts, so only that node gets the search highlight.
    return <MemoryPanel highlightId={id === "mem_facts" ? focusFact : null} />;
  if (node?.type === "voice") return <VoicePanel />;
  return <div className="insp-note">No live controls for this node.</div>;
}

function StatRow({ k, v }: { k: string; v: React.ReactNode }) {
  return (
    <div className="stat-row">
      <span className="stat-k">{k}</span>
      <span className="stat-v">{v}</span>
    </div>
  );
}

function ToolControls({
  id,
  stats,
  refresh,
}: {
  id: string;
  stats: NodeStats | null;
  refresh: () => void;
}) {
  const name = id.slice("tool:".length);
  const enabled = stats?.enabled !== false;
  const toggle = async () => {
    await setToolFlag(name, !enabled).catch(() => {});
    refresh();
  };
  return (
    <div className="insp-section">
      <label className="toggle">
        <input type="checkbox" checked={enabled} onChange={toggle} />
        <span>{enabled ? "Enabled" : "Disabled"} (model {enabled ? "sees" : "can't see"} this tool)</span>
      </label>
      {stats && (
        <div className="stats">
          <StatRow k="calls today" v={stats.calls_today ?? 0} />
          <StatRow k={`calls (${stats.window_days ?? 7}d)`} v={stats.calls_window ?? 0} />
          <StatRow k="error rate" v={`${Math.round((stats.error_rate ?? 0) * 100)}%`} />
          <StatRow k="p50 / p95" v={`${fmt(stats.p50_ms)} / ${fmt(stats.p95_ms)} ms`} />
          <StatRow k="last used" v={stats.last_ts ?? "—"} />
        </div>
      )}
    </div>
  );
}

function BrainControls({ id, stats }: { id: string; stats: NodeStats | null }) {
  const boostArmed = useBrain((s) => s.boostArmed);
  const isHeavy = id === "brain:nim_heavy";
  return (
    <div className="insp-section">
      {stats && (
        <div className="stats">
          <StatRow k="latency p50 / p95" v={`${fmt(stats.latency_ms?.p50)} / ${fmt(stats.latency_ms?.p95)} ms`} />
          <StatRow k="tokens today" v={stats.tokens?.total ?? 0} />
          <StatRow k="turns" v={stats.turns ?? 0} />
          <StatRow k="currently active" v={stats.current ? "yes" : "no"} />
          <StatRow k="router health" v={stats.router_state ?? "—"} />
        </div>
      )}
      {isHeavy && (
        <button
          className={"boost-btn" + (boostArmed ? " on" : "")}
          onClick={() =>
            boostArmed ? useBrain.getState().disarmBoost() : useBrain.getState().armBoost()
          }
          title="Prefer the strongest available brain for the next turn (subordinate to privacy/health pins)"
        >
          {boostArmed ? "⚡ boost armed — cancel" : "⚡ prefer strongest brain next turn"}
        </button>
      )}
    </div>
  );
}

function GatePanel() {
  return (
    <div className="insp-section">
      <div className="insp-note gate-note">
        The safety gate classifies every tool call (allow / confirm / deny). It
        <strong> cannot be disabled or bypassed</strong> — enforced in code and covered
        by a test.
      </div>
    </div>
  );
}

function TaskQueuePanel({ stats, refresh }: { stats: NodeStats | null; refresh: () => void }) {
  const cancel = async (tid: number) => {
    await cancelTask(tid).catch(() => {});
    refresh();
  };
  const tasks = stats?.tasks ?? [];
  return (
    <div className="insp-section">
      {stats && (
        <div className="stats">
          <StatRow k="running" v={stats.running ?? 0} />
          <StatRow k="queued" v={stats.queued ?? 0} />
        </div>
      )}
      <div className="task-list">
        {tasks.length === 0 && <div className="insp-note">No tasks.</div>}
        {tasks.map((t) => (
          <div key={t.id} className="task-row">
            <span className={`task-status ${t.status}`}>{t.status}</span>
            <span className="task-title">{t.title}</span>
            {(t.status === "queued" || t.status === "running") && (
              <button className="task-cancel" onClick={() => cancel(t.id)}>
                cancel
              </button>
            )}
          </div>
        ))}
      </div>
    </div>
  );
}

function SchedulerPanel({ stats }: { stats: NodeStats | null }) {
  const jobs = stats?.jobs ?? [];
  const run = async (jobId: string) => {
    await runSchedule(jobId).catch(() => {});
    useBrain.getState().pushToast(`ran ${jobId}`);
  };
  return (
    <div className="insp-section">
      <div className="task-list">
        {jobs.length === 0 && <div className="insp-note">No scheduled jobs.</div>}
        {jobs.map((j) => (
          <div key={j.id} className="task-row">
            <span className="task-title">{j.id}</span>
            <span className="job-next">{j.next_run || "—"}</span>
            <button className="task-cancel" onClick={() => run(j.id)}>
              run now
            </button>
          </div>
        ))}
      </div>
    </div>
  );
}

function VoicePanel() {
  return (
    <div className="insp-section">
      <div className="insp-note">
        Live voice metrics appear when Baby runs with <code>--voice</code>. In
        text-only mode this node shows its description only.
      </div>
    </div>
  );
}

function RecentEvents({ events }: { events: LiveEvent[] }) {
  if (!events.length) return null;
  return (
    <div className="insp-section">
      <div className="insp-subhead">Recent activity</div>
      <div className="insp-events">
        {events
          .slice()
          .reverse()
          .map((e) => (
            <div key={e.seq} className="insp-event">
              <span className="ev-kind">{e.kind}</span>
              <span className="ev-detail">
                {String((e.payload as { tool?: string; text?: string }).tool ?? "") ||
                  String((e.payload as { text?: string }).text ?? "")}
              </span>
            </div>
          ))}
      </div>
    </div>
  );
}

function fmt(v: number | null | undefined): string {
  return v == null ? "—" : String(v);
}
