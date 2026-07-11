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
import type { Tier } from "./graph/governor/tierMachine";
import type { VramSignal } from "./graph/governor/vramWatchdog";
import { backoffDelayMs, nextLossCount } from "./graph/sphere/contextLossBackoff";

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

// Context-loss retry state (V3f), module-level so it never triggers a render. The
// count climbs while losses keep recurring (bounded backoff) and resets to the short
// fuse when a loss arrives only after a long clean gap — see setContextLost.
let _ctxLossCount = 0;
let _ctxLastLossTs = 0; // 0 = no loss yet
let _ctxRetryTimer: ReturnType<typeof setTimeout> | undefined;

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
  /** V2 frame governor: VRAM signal off /ws/state (null = no NVML / not seen yet). */
  vram: VramSignal | null;
  /** V3 watchdog: local model resident in VRAM? (null = unknown → fail-open full3d). */
  localModelLoaded: boolean | null;
  /** V3 context-loss floor: WebGL context died → force the 2D graph (backoff retry). */
  contextLost: boolean;
  /** V2 governor: current quality tier (full3d → lite3d → 2d floor). */
  renderTier: Tier;
  /** V2 governor: config ceiling from render.tier ("auto" → full3d). */
  renderCeiling: Tier;
  /** V2 governor: target fps from render.target_fps (default 60). */
  targetFps: number;
  events: LiveEvent[];
  selectedNode: string | null;
  /** Full topology, lifted from BrainGraph so the inspector drawer can resolve ids. */
  graph: GraphData | null;
  /** B4: the one-turn best-brain boost is armed (mirrors the server one-shot). */
  boostArmed: boolean;
  /** B5: fact id to best-effort-highlight in the memory panel (search fly-to). */
  focusFact: number | null;
  /** v5: the live conversation id (sidebar "active" highlight; null until known). */
  activeConversationId: number | null;
  /** v5: conversation being VIEWED read-only (null = the live transcript). While
   *  non-null, the streaming chat reducers no-op so a live turn on the active chat
   *  can't corrupt the frozen viewed transcript. */
  viewingConversationId: number | null;

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
  setVram: (v: VramSignal | null) => void;
  /** null = back to unknown (e.g. a /stats reply without the field) → fail-open. */
  setLocalModelLoaded: (on: boolean | null) => void;
  /**
   * Mark the WebGL context lost/restored. A loss schedules ONE retry on a bounded
   * backoff (60 s → 2 m → 5 m) that climbs while losses keep recurring; a loss after a
   * long clean gap resets to the 60 s fuse so a recovered GPU recovers promptly.
   */
  setContextLost: (lost: boolean) => void;
  setRenderTier: (t: Tier) => void;
  setRenderCeiling: (t: Tier) => void;
  setTargetFps: (fps: number) => void;
  pushEvent: (e: LiveEvent) => void;
  selectNode: (id: string | null) => void;
  setGraph: (g: GraphData) => void;
  armBoost: () => void;
  disarmBoost: () => void;
  setFocusFact: (id: number | null) => void;
  setActiveConversationId: (id: number | null) => void;
  setViewing: (id: number | null) => void;
  /** v5: replace the whole transcript (past-chat viewer + resume/new backfill). */
  setTranscript: (rows: ChatMessage[]) => void;

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
  vram: null,
  localModelLoaded: null,
  contextLost: false,
  renderTier: "full3d",
  renderCeiling: "full3d",
  targetFps: 60,
  events: [],
  selectedNode: null,
  graph: null,
  boostArmed: false,
  focusFact: null,
  activeConversationId: null,
  viewingConversationId: null,

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
  setVram: (v) => set({ vram: v }),
  setLocalModelLoaded: (on) =>
    set((st) => (st.localModelLoaded === on ? {} : { localModelLoaded: on })),
  setContextLost: (lost) =>
    set((st) => {
      if (st.contextLost === lost) return {};
      if (lost) {
        // A LIVE context died → floor to 2D and schedule ONE retry on the bounded
        // fuse. The gap since the previous loss decides the fuse: a loss that recurs
        // quickly (a genuinely broken GPU, often the local 9B holding VRAM for a whole
        // offline turn) climbs 60 s → 2 m → 5 m so it quiesces instead of hot-looping
        // Canvas creation; a loss after a long clean stretch is an isolated blip and
        // starts back at 60 s (a recovered GPU recovers promptly). Keying on the
        // inter-loss gap — not "did the remount survive a few seconds" — is what makes
        // a flaky GPU (fresh context dies after ~10 s) actually escalate.
        if (_ctxRetryTimer !== undefined) clearTimeout(_ctxRetryTimer);
        // performance.now() (monotonic) so an NTP/wall-clock jump can't skew the gap.
        const now = performance.now();
        const gap = _ctxLastLossTs === 0 ? Infinity : now - _ctxLastLossTs;
        _ctxLossCount = nextLossCount(_ctxLossCount, gap);
        _ctxLastLossTs = now;
        _ctxRetryTimer = setTimeout(() => {
          _ctxRetryTimer = undefined;
          useBrain.getState().setContextLost(false);
        }, backoffDelayMs(_ctxLossCount));
      }
      return { contextLost: lost };
    }),
  setRenderTier: (t) => set((st) => (st.renderTier === t ? {} : { renderTier: t })),
  setRenderCeiling: (t) => set((st) => (st.renderCeiling === t ? {} : { renderCeiling: t })),
  setTargetFps: (fps) => set((st) => (st.targetFps === fps ? {} : { targetFps: fps })),
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
  setActiveConversationId: (id) =>
    set((st) => (st.activeConversationId === id ? {} : { activeConversationId: id })),
  setViewing: (id) =>
    set((st) => (st.viewingConversationId === id ? {} : { viewingConversationId: id })),
  setTranscript: (rows) => set({ messages: capMessages(rows) }),

  // Every streaming reducer below no-ops while a past chat is being VIEWED
  // (viewingConversationId != null) — a live turn on the active conversation must
  // not append into the frozen read-only transcript (v5).
  addUserMessage: (text) =>
    set((st) =>
      st.viewingConversationId !== null
        ? {}
        : { messages: capMessages([...st.messages, { role: "user", text }]) },
    ),

  // turn_start: open an empty streaming assistant bubble.
  startTurn: () =>
    set((st) =>
      st.viewingConversationId !== null
        ? {}
        : {
            messages: capMessages([
              ...st.messages,
              { role: "assistant", text: "", streaming: true },
            ]),
          },
    ),

  // token: append to the open streaming bubble (create one if a token races
  // ahead of turn_start, matching the classic UI's defensive check).
  appendToken: (text) =>
    set((st) => {
      if (st.viewingConversationId !== null) return {};
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
      if (st.viewingConversationId !== null) return {};
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
    set((st) =>
      st.viewingConversationId !== null
        ? {}
        : { messages: capMessages([...st.messages, { role: "system", text }]) },
    ),

  loadHistory: (rows) =>
    set((st) =>
      st.viewingConversationId !== null
        ? {}
        : { messages: capMessages([...rows, ...st.messages]) },
    ),

  // Chat socket dropped mid-turn: close the open streaming bubble so the blinking
  // cursor stops, and drop a one-line system note. No-op when nothing is streaming
  // (an idle-time drop needs no chat note — the header pill carries that signal).
  interruptTurn: () =>
    set((st) => {
      if (st.viewingConversationId !== null) return {};
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
