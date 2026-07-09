import { create } from "zustand";
import type {
  Brain,
  ChatMessage,
  ConfirmReq,
  LiveEvent,
  PipelineState,
  RouterHealth,
  Stats,
  Toast,
  Tokens,
} from "./types";

/**
 * The single v3 store (spec §3). B0 defined the shell fields; B2 adds the chat
 * transcript, confirmation state, /stats snapshot, toasts, and dialog/panel UI
 * flags. The live-event ring buffer, pipeline state, router health, and node
 * selection carry over. Reducers here are the vitest target (pure logic).
 */

export type { PipelineState, RouterHealth, LiveEvent } from "./types";

const EVENT_RING_CAP = 500; // long-session hygiene (spec §11): never unbounded

let _toastSeq = 0;
let _eventSeq = 0;

export type RightTab = "chat" | "activity";

interface BrainState {
  /** true once the v3 shell has talked to the backend at least once. */
  connected: boolean;
  pipeline: PipelineState;
  router: RouterHealth;
  gameMode: boolean;
  events: LiveEvent[];
  selectedNode: string | null;

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

  setConnected: (v: boolean) => void;
  setPipeline: (s: PipelineState) => void;
  setRouter: (r: RouterHealth) => void;
  setGameMode: (on: boolean) => void;
  pushEvent: (e: LiveEvent) => void;
  selectNode: (id: string | null) => void;

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
  connected: false,
  pipeline: "idle",
  router: "unknown",
  gameMode: false,
  events: [],
  selectedNode: null,

  messages: [],
  activeConfirm: null,
  stats: null,
  toasts: [],
  memoryOpen: false,
  rightTab: "chat",
  rightCollapsed: false,

  setConnected: (v) => set({ connected: v }),
  setPipeline: (s) => set({ pipeline: s }),
  setRouter: (r) => set({ router: r }),
  setGameMode: (on) => set({ gameMode: on }),
  pushEvent: (e) =>
    set((st) => {
      const events =
        st.events.length >= EVENT_RING_CAP
          ? [...st.events.slice(st.events.length - EVENT_RING_CAP + 1), e]
          : [...st.events, e];
      return { events };
    }),
  selectNode: (id) => set({ selectedNode: id }),

  addUserMessage: (text) =>
    set((st) => ({ messages: [...st.messages, { role: "user", text }] })),

  // turn_start: open an empty streaming assistant bubble.
  startTurn: () =>
    set((st) => ({
      messages: [...st.messages, { role: "assistant", text: "", streaming: true }],
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
      return { messages: msgs };
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
    set((st) => ({ messages: [...st.messages, { role: "system", text }] })),

  loadHistory: (rows) =>
    set((st) => ({ messages: [...rows, ...st.messages] })),

  openConfirm: (req) => set({ activeConfirm: req }),
  clearConfirm: (id) =>
    set((st) => {
      if (id && st.activeConfirm && st.activeConfirm.confirm_id !== id) return {};
      return { activeConfirm: null };
    }),

  setStats: (s) => set({ stats: s, connected: true }),

  pushToast: (text, kind = "info") =>
    set((st) => ({ toasts: [...st.toasts, { id: ++_toastSeq, text, kind }] })),
  dismissToast: (id) =>
    set((st) => ({ toasts: st.toasts.filter((t) => t.id !== id) })),

  openMemory: () => set({ memoryOpen: true }),
  closeMemory: () => set({ memoryOpen: false }),
  setTab: (t) => set({ rightTab: t, rightCollapsed: false }),
  toggleRightPanel: () => set((st) => ({ rightCollapsed: !st.rightCollapsed })),
}));

/** Next client-side sequence number for the live-event ring (frames carry no seq). */
export const nextEventSeq = (): number => ++_eventSeq;
