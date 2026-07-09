import { create } from "zustand";
import type {
  Brain,
  ChatMessage,
  ConfirmReq,
  GraphData,
  LiveEvent,
  PipelineState,
  RouterHealth,
  Stats,
  Toast,
  Tokens,
} from "./types";
import { setBoost } from "./api/client";

/**
 * The single v3 store (spec §3). B0 defined the shell fields; B2 adds the chat
 * transcript, confirmation state, /stats snapshot, toasts, and dialog/panel UI
 * flags. The live-event ring buffer, pipeline state, router health, and node
 * selection carry over. Reducers here are the vitest target (pure logic).
 */

export type { PipelineState, RouterHealth, LiveEvent } from "./types";

const EVENT_RING_CAP = 500; // long-session hygiene (spec §11): never unbounded
const MESSAGE_CAP = 300; // B7: cap the transcript so a multi-hour session stays bounded
const TOAST_CAP = 5; // B7: bound the toast stack (auto-dismiss aside)

let _toastSeq = 0;
let _eventSeq = 0;

const PERF_KEY = "baby.performanceMode";
function loadPerfMode(): boolean {
  try {
    return localStorage.getItem(PERF_KEY) === "1";
  } catch {
    return false;
  }
}

// Front-trim to the newest MESSAGE_CAP — slicing off the head always keeps the
// tail, so a still-streaming bubble (always last) is never dropped mid-turn.
function capMessages(msgs: ChatMessage[]): ChatMessage[] {
  return msgs.length > MESSAGE_CAP ? msgs.slice(msgs.length - MESSAGE_CAP) : msgs;
}

// Default the right panel collapsed on a phone-width viewport so the graph stays
// full-bleed; on desktop it opens inline as before (B7 responsive).
function initCollapsed(): boolean {
  try {
    return window.innerWidth <= 720;
  } catch {
    return false;
  }
}

export type RightTab = "chat" | "activity";

/** Per-channel WebSocket liveness (B7): honest reconnect signal, was a dead flag. */
export interface WsStatus {
  chat: boolean;
  activity: boolean;
  state: boolean;
}

interface BrainState {
  /** Per-channel WS liveness — the honest reconnect signal for all three sockets. */
  ws: WsStatus;
  pipeline: PipelineState;
  router: RouterHealth;
  gameMode: boolean;
  /** Tier of the brain that authored the last turn (remapped graph id, e.g. brain:cloud). */
  activeBrain: string | null;
  /** B3 perf opt-in: stop the render clock when quiet, no particles, static core. */
  performanceMode: boolean;
  events: LiveEvent[];
  selectedNode: string | null;
  /** Full topology, lifted from BrainGraph so the inspector drawer can resolve ids. */
  graph: GraphData | null;
  /** B4: the one-turn best-brain boost is armed (mirrors the server one-shot). */
  boostArmed: boolean;
  /** B5: fact id to best-effort-highlight in the memory panel (search fly-to). */
  focusFact: number | null;

  // chat
  messages: ChatMessage[];
  // gating
  activeConfirm: ConfirmReq | null;
  // header
  stats: Stats | null;
  // transient notices
  toasts: Toast[];
  // UI chrome
  memoryOpen: boolean;
  rightTab: RightTab;
  rightCollapsed: boolean;

  setWsStatus: (chan: keyof WsStatus, up: boolean) => void;
  setPipeline: (s: PipelineState) => void;
  setRouter: (r: RouterHealth) => void;
  setGameMode: (on: boolean) => void;
  setActiveBrain: (id: string | null) => void;
  togglePerformanceMode: () => void;
  pushEvent: (e: LiveEvent) => void;
  selectNode: (id: string | null) => void;
  setGraph: (g: GraphData) => void;
  armBoost: () => void;
  disarmBoost: () => void;
  setFocusFact: (id: number | null) => void;

  // chat reducers
  addUserMessage: (text: string) => void;
  startTurn: () => void;
  appendToken: (text: string) => void;
  finishTurn: (p: {
    reply?: string;
    status?: string;
    brain?: Brain;
    tokens?: Tokens;
  }) => void;
  addSystemNote: (text: string) => void;
  loadHistory: (rows: ChatMessage[]) => void;
  /** Finalize a mid-stream bubble on a dropped chat socket (kills the stuck cursor). */
  interruptTurn: () => void;

  // gating
  openConfirm: (req: ConfirmReq) => void;
  clearConfirm: (id?: string) => void;

  // header
  setStats: (s: Stats) => void;

  // toasts
  pushToast: (text: string, kind?: "info" | "error") => void;
  dismissToast: (id: number) => void;

  // chrome
  openMemory: () => void;
  closeMemory: () => void;
  setTab: (t: RightTab) => void;
  toggleRightPanel: () => void;
}

export const useBrain = create<BrainState>((set) => ({
  ws: { chat: false, activity: false, state: false },
  pipeline: "idle",
  router: "unknown",
  gameMode: false,
  activeBrain: null,
  performanceMode: loadPerfMode(),
  events: [],
  selectedNode: null,
  graph: null,
  boostArmed: false,
  focusFact: null,

  messages: [],
  activeConfirm: null,
  stats: null,
  toasts: [],
  memoryOpen: false,
  rightTab: "chat",
  rightCollapsed: initCollapsed(),

  setWsStatus: (chan, up) =>
    set((st) => (st.ws[chan] === up ? {} : { ws: { ...st.ws, [chan]: up } })),
  setPipeline: (s) => set({ pipeline: s }),
  setRouter: (r) => set({ router: r }),
  setGameMode: (on) => set({ gameMode: on }),
  setActiveBrain: (id) => set({ activeBrain: id }),
  togglePerformanceMode: () =>
    set((st) => {
      const next = !st.performanceMode;
      try {
        localStorage.setItem(PERF_KEY, next ? "1" : "0");
      } catch {
        /* private mode / no storage — fine */
      }
      return { performanceMode: next };
    }),
  pushEvent: (e) =>
    set((st) => {
      const events =
        st.events.length >= EVENT_RING_CAP
          ? [...st.events.slice(st.events.length - EVENT_RING_CAP + 1), e]
          : [...st.events, e];
      return { events };
    }),
  // Selecting anything other than mem_facts drops a pending fact highlight, so a
  // search-driven focus never leaks onto a later manual open (B5).
  selectNode: (id) =>
    set((st) => ({
      selectedNode: id,
      focusFact: id === "mem_facts" ? st.focusFact : null,
    })),
  setGraph: (g) => set({ graph: g }),
  armBoost: () => {
    setBoost(true).catch(() => {});
    set({ boostArmed: true });
  },
  disarmBoost: () => {
    setBoost(false).catch(() => {});
    set({ boostArmed: false });
  },
  setFocusFact: (id) => set({ focusFact: id }),

  addUserMessage: (text) =>
    set((st) => ({ messages: capMessages([...st.messages, { role: "user", text }]) })),

  // turn_start: open an empty streaming assistant bubble.
  startTurn: () =>
    set((st) => ({
      messages: capMessages([
        ...st.messages,
        { role: "assistant", text: "", streaming: true },
      ]),
    })),

  // token: append to the open streaming bubble (create one if a token races
  // ahead of turn_start, matching the classic UI's defensive check).
  appendToken: (text) =>
    set((st) => {
      const msgs = st.messages.slice();
      const last = msgs[msgs.length - 1];
      if (last && last.role === "assistant" && last.streaming) {
        msgs[msgs.length - 1] = { ...last, text: last.text + text };
      } else {
        msgs.push({ role: "assistant", text, streaming: true });
      }
      return { messages: capMessages(msgs) };
    }),

  // turn_end: the server reply is the scrubbed truth — swap the streamed text
  // for it (raw stream can leak <think> and the late "Next:" suggestion), then
  // attach the brain + token badges and stop streaming.
  finishTurn: ({ reply, brain, tokens }) =>
    set((st) => {
      const msgs = st.messages.slice();
      for (let i = msgs.length - 1; i >= 0; i--) {
        const m = msgs[i];
        if (m.role === "assistant" && m.streaming) {
          const text = reply || m.text || "…";
          msgs[i] = { ...m, text, brain, tokens, streaming: false };
          return { messages: msgs };
        }
      }
      return {};
    }),

  addSystemNote: (text) =>
    set((st) => ({ messages: capMessages([...st.messages, { role: "system", text }]) })),

  loadHistory: (rows) =>
    set((st) => ({ messages: capMessages([...rows, ...st.messages]) })),

  // Chat socket dropped mid-turn: close the open streaming bubble so the blinking
  // cursor stops, and drop a one-line system note. No-op when nothing is streaming
  // (an idle-time drop needs no chat note — the header pill carries that signal).
  interruptTurn: () =>
    set((st) => {
      const msgs = st.messages.slice();
      for (let i = msgs.length - 1; i >= 0; i--) {
        const m = msgs[i];
        if (m.role === "assistant" && m.streaming) {
          msgs[i] = { ...m, text: m.text || "…", streaming: false };
          return {
            messages: capMessages([
              ...msgs,
              { role: "system", text: "Connection lost — reconnecting…" },
            ]),
          };
        }
      }
      return {};
    }),

  openConfirm: (req) => set({ activeConfirm: req }),
  clearConfirm: (id) =>
    set((st) => {
      if (id && st.activeConfirm && st.activeConfirm.confirm_id !== id) return {};
      return { activeConfirm: null };
    }),

  setStats: (s) => set({ stats: s }),

  pushToast: (text, kind = "info") =>
    set((st) => {
      const toasts = [...st.toasts, { id: ++_toastSeq, text, kind }];
      return {
        toasts:
          toasts.length > TOAST_CAP ? toasts.slice(toasts.length - TOAST_CAP) : toasts,
      };
    }),
  dismissToast: (id) =>
    set((st) => ({ toasts: st.toasts.filter((t) => t.id !== id) })),

  openMemory: () => set({ memoryOpen: true }),
  closeMemory: () => set({ memoryOpen: false }),
  setTab: (t) => set({ rightTab: t, rightCollapsed: false }),
  toggleRightPanel: () => set((st) => ({ rightCollapsed: !st.rightCollapsed })),
}));

/** Next client-side sequence number for the live-event ring (frames carry no seq). */
export const nextEventSeq = (): number => ++_eventSeq;
