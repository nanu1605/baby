import { useEffect, useMemo, useRef } from "react";
import { useBrain } from "../store";
import { ICONS, TASK_LABEL, TOOL_MARKS } from "../constants";
import type { LiveEvent } from "../types";

/**
 * The activity feed — a port of the classic vanilla feed off the live-event ring
 * buffer. Tool calls fold start→end into one row (colored by safety class);
 * status / task / project events render as system lines. In B3 the graph
 * supersedes this as the primary surface; it stays as the log.
 */

interface ToolItem {
  kind: "tool";
  callId: string;
  tool: string;
  args: unknown;
  safetyClass: string;
  glyph: string;
  result: string;
}
interface LineItem {
  kind: "line";
  key: string;
  text: string;
}
type Item = ToolItem | LineItem;

function str(v: unknown): string {
  return typeof v === "string" ? v : "";
}

function buildActivity(events: LiveEvent[]): Item[] {
  const items: Item[] = [];
  const toolIndex = new Map<string, number>();

  for (const ev of events) {
    const p = ev.payload;
    switch (ev.kind) {
      case "tool_start": {
        const callId = str(p.call_id);
        toolIndex.set(callId, items.length);
        items.push({
          kind: "tool",
          callId,
          tool: str(p.tool) || "tool",
          args: p.args,
          safetyClass: str(p.safety_class) || "allow",
          glyph: "⏳",
          result: "",
        });
        break;
      }
      case "tool_end": {
        const idx = toolIndex.get(str(p.call_id));
        if (idx != null) {
          const it = items[idx] as ToolItem;
          const status = str(p.status);
          it.glyph = TOOL_MARKS[status] || status || "✓";
          it.result = str(p.result_summary);
        }
        break;
      }
      case "status":
        items.push({ kind: "line", key: `s${ev.seq}`, text: str(p.text) });
        break;
      case "task_queued":
      case "task_started":
      case "task_done": {
        const tail =
          ev.kind === "task_done"
            ? ` (${str(p.status)}${p.result_summary ? ": " + str(p.result_summary) : ""})`
            : "";
        items.push({
          kind: "line",
          key: `t${ev.seq}`,
          text: `task #${String(p.task_id ?? "")} ${TASK_LABEL[ev.kind]}: ${str(p.title)}${tail}`,
        });
        break;
      }
      case "project_started":
        items.push({
          kind: "line",
          key: `p${ev.seq}`,
          text: `project #${String(p.project_id ?? "")} started: ${str(p.title)} (${String(p.subtasks ?? 0)} subtasks)`,
        });
        break;
      case "project_done":
        items.push({
          kind: "line",
          key: `p${ev.seq}`,
          text: `project #${String(p.project_id ?? "")} ${str(p.status)}: ${str(p.title)}${p.result_summary ? ": " + str(p.result_summary) : ""}`,
        });
        break;
    }
  }
  return items;
}

export default function ActivityPanel() {
  const events = useBrain((s) => s.events);
  const items = useMemo(() => buildActivity(events), [events]);
  const endRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    endRef.current?.scrollIntoView({ block: "end" });
  }, [items.length]);

  return (
    <div className="activity-panel">
      {items.length === 0 && (
        <div className="empty-hint">No activity yet — tool calls appear here.</div>
      )}
      {items.map((it, i) =>
        it.kind === "tool" ? (
          <div key={`${it.callId}:${i}`} className={`entry ${it.safetyClass}`}>
            <div className="head">
              <span>{ICONS[it.tool] || "⚙"}</span>
              <span className="tool-name">{it.tool}</span>
              <span className="state">{it.glyph}</span>
            </div>
            <details>
              <summary>args</summary>
              <pre>{JSON.stringify(it.args, null, 2)}</pre>
            </details>
            {it.result && <div className="result">{it.result}</div>}
          </div>
        ) : (
          <div key={it.key} className="entry system-line">
            {it.text}
          </div>
        ),
      )}
      <div ref={endRef} />
    </div>
  );
}
