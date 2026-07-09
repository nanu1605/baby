/**
 * UI constants ported verbatim from the classic vanilla UI (ui/web/app.js) so
 * v3 keeps daily-driver parity: brain chip labels, tool icons, router labels,
 * tool-result glyphs, task-event labels.
 */
import type { RouterHealth } from "./types";

/** tier -> [label, css class]. From app.js BRAIN_LABELS. */
export const BRAIN_LABELS: Record<string, [string, string]> = {
  daily: ["local", "local"],
  nim_primary: ["cloud", "nim"],
  nim_heavy: ["NIM heavy", "nim"],
  backstop: ["Gemini", "gemini"],
};

/** tool name -> icon. From app.js ICONS. */
export const ICONS: Record<string, string> = {
  run_shell: "⌨",
  app_control: "🗔",
  file_search: "🔍",
  read_file: "📄",
  write_file: "✏",
  web_search: "🌐",
  fetch_page: "🌐",
  get_system_stats: "📊",
  get_time: "🕐",
};

/** router state -> header label. From app.js ROUTER_LABEL. */
export const ROUTER_LABEL: Record<string, string> = {
  cloud: "cloud",
  degraded: "cloud degraded",
  offline: "cloud offline",
  unknown: "cloud —",
};

/** tool_end status -> glyph. From app.js `marks`. */
export const TOOL_MARKS: Record<string, string> = {
  ok: "✓",
  dry_run: "✓ (dry)",
  error: "✗",
  denied: "⛔",
  refused: "🚫",
  timeout: "🚫 (timeout)",
};

/** task_* event kind -> label. From app.js TASK_LABEL. */
export const TASK_LABEL: Record<string, string> = {
  task_queued: "queued",
  task_started: "started",
  task_done: "finished",
};

/** Coerce a raw router string into the health union. */
export function normRouter(s: string | undefined | null): RouterHealth {
  if (s === "cloud" || s === "degraded" || s === "offline") return s;
  return "unknown";
}
