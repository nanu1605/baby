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
