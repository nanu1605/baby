/**
 * Shared v3 types. Mirrors the backend contracts verified for B2:
 * - `/api/graph` (core/nodes.py) — heterogeneous nodes, discriminate on `type`.
 * - `/ws/chat` + `/ws/activity` frames = {type, ts, channel, ...payload}.
 * - `/ws/state` frame = {type:"state", state, router, game_mode}.
 * - `/stats` (ui/server.py) — the header snapshot.
 */

export type NodeType =
  | "core"
  | "router"
  | "safety"
  | "memory"
  | "voice"
  | "infra"
  | "brain"
  | "tool";

export type GroupName =
  | "core"
  | "voice"
  | "brains"
  | "tools"
  | "memory"
  | "infra";

/** A node from /api/graph. brain adds tier/provider/model; tool adds safety_class. */
export interface GraphNode {
  id: string;
  type: NodeType;
  group: GroupName;
  label: string;
  role: string;
  blurb: string;
  tier?: string;
  provider?: string;
  model?: string;
  safety_class?: string;
}

export interface GraphEdge {
  source: string;
  target: string;
}

export interface GraphData {
  nodes: GraphNode[];
  edges: GraphEdge[];
}

export type PipelineState =
  | "idle"
  | "listening"
  | "thinking"
  | "speaking"
  | "executing";

export type RouterHealth = "cloud" | "degraded" | "offline" | "unknown";

/** Which brain answered a turn (rides on turn_end.brain). */
export interface Brain {
  tier?: string;
  model?: string;
  reason?: string;
}

export interface Tokens {
  prompt: number;
  completion: number;
  total: number;
}

export type Role = "user" | "assistant" | "system";

export interface ChatMessage {
  role: Role;
  text: string;
  brain?: Brain;
  tokens?: Tokens;
  streaming?: boolean;
}

export interface ConfirmReq {
  confirm_id: string;
  tool?: string;
  command: string;
  explanation: string;
  timeout_s: number;
}

export interface Toast {
  id: number;
  text: string;
  kind: "info" | "error";
}

export interface MemoryFact {
  id: number;
  text: string;
  source?: string;
  created_at?: string;
  last_used_at?: string;
  active: boolean;
}

/** Partial /stats shape — only what the header reads. */
export interface Stats {
  model?: string;
  cpu_percent?: number;
  ram?: { used_gb: number; total_gb: number; percent: number };
  gpu?: {
    name: string;
    util_percent: number;
    vram_used_gb: number;
    vram_total_gb: number;
  } | null;
  router?: { state?: string } | null;
  game_mode?: boolean;
  turn_running?: boolean;
  /** V2 frame governor knobs (code-defaulted server-side). */
  render?: { target_fps: number; tier: string; idle_full_on_desktop: boolean };
  /** V3 sphere gate — ui.brain (code-defaulted "3d"; "2d" = v3 canvas rollback). */
  ui?: { brain: string };
  /** V3 watchdog: local model resident in VRAM (omitted while unknown). */
  local_model_loaded?: boolean;
  tokens?: {
    session: {
      prompt: number;
      completion: number;
      total: number;
      by_brain?: Record<string, number>;
    };
    today: {
      prompt: number;
      completion: number;
      total: number;
      by_brain?: Record<string, number>;
    };
  };
}

export interface TaskRow {
  id: number;
  title: string;
  status: string;
  [k: string]: unknown;
}

/** Loose union of `/api/nodes/{id}/stats` payloads (fields present per node type). */
export interface NodeStats {
  id: string;
  type: string;
  // tool
  enabled?: boolean;
  calls_today?: number;
  calls_window?: number;
  window_days?: number;
  errors?: number;
  error_rate?: number;
  p50_ms?: number | null;
  p95_ms?: number | null;
  last_ts?: string | null;
  // brain
  latency_ms?: { p50: number | null; p95: number | null };
  tokens?: {
    prompt: number;
    completion: number;
    total: number;
    turns?: number;
    window_days?: number;
  };
  turns?: number;
  current?: boolean;
  router_state?: string | null;
  pinned_next_turn?: boolean;
  // task_queue
  running?: number;
  queued?: number;
  tasks?: TaskRow[];
  // scheduler
  jobs?: { id: string; next_run: string }[];
  // memory
  facts?: number;
}

// -- B5 search omnibox (GET /api/search — grouped FTS/vector results) ---------

export type SearchGroupKey = "facts" | "conversations" | "activity" | "tasks";
export type SearchItemType = "fact" | "conversation" | "activity" | "task";

/** Every result the server stamps with its anchor `node_id`; `ts` is null for facts. */
interface SearchItemBase {
  id: number;
  snippet: string;
  ts: string | null;
  node_id: string;
}
export interface FactResult extends SearchItemBase {
  type: "fact";
}
export interface ConversationResult extends SearchItemBase {
  type: "conversation";
  conversation_id?: number | null;
}
export interface ActivityResult extends SearchItemBase {
  type: "activity";
}
export interface TaskResult extends SearchItemBase {
  type: "task";
  status?: string;
}
export type SearchItem =
  | FactResult
  | ConversationResult
  | ActivityResult
  | TaskResult;

export interface SearchGroups {
  facts: FactResult[];
  conversations: ConversationResult[];
  activity: ActivityResult[];
  tasks: TaskResult[];
}

export interface SearchResponse {
  query: string;
  groups: SearchGroups;
}

/** One entry in the bounded live-event ring buffer. */
export interface LiveEvent {
  seq: number;
  kind: string;
  channel: string;
  ts: string;
  source?: string;
  target?: string;
  turnId?: number;
  payload: Record<string, unknown>;
}

/** Every /ws/chat + /ws/activity frame. Extra keys ride at top level. */
export interface WSFrame {
  type: string;
  ts?: string;
  channel?: string;
  [k: string]: unknown;
}
