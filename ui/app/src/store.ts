import { create } from "zustand";

/**
 * The single v3 store (spec §3). Kept deliberately small in B0 — the graph
 * topology, live-event ring buffer, pipeline state, and inspector selection
 * all land here as later phases wire them up. Defining the shape now keeps the
 * store coherent across B1–B5 instead of growing ad hoc.
 */

export type PipelineState =
  | "idle"
  | "listening"
  | "thinking"
  | "speaking"
  | "executing";

export type RouterHealth = "cloud" | "degraded" | "offline" | "unknown";

/** One entry in the bounded live-event ring buffer (B3 feeds this). */
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

const EVENT_RING_CAP = 500; // long-session hygiene (spec §11): never unbounded

interface BrainState {
  /** true once the v3 shell has talked to the backend at least once. */
  connected: boolean;
  pipeline: PipelineState;
  router: RouterHealth;
  gameMode: boolean;
  events: LiveEvent[];
  selectedNode: string | null;

  setConnected: (v: boolean) => void;
  setPipeline: (s: PipelineState) => void;
  setRouter: (r: RouterHealth) => void;
  setGameMode: (on: boolean) => void;
  pushEvent: (e: LiveEvent) => void;
  selectNode: (id: string | null) => void;
}

export const useBrain = create<BrainState>((set) => ({
  connected: false,
  pipeline: "idle",
  router: "unknown",
  gameMode: false,
  events: [],
  selectedNode: null,

  setConnected: (v) => set({ connected: v }),
  setPipeline: (s) => set({ pipeline: s }),
  setRouter: (r) => set({ router: r }),
  setGameMode: (on) => set({ gameMode: on }),
  pushEvent: (e) =>
    set((st) => {
      const events = st.events.length >= EVENT_RING_CAP
        ? [...st.events.slice(st.events.length - EVENT_RING_CAP + 1), e]
        : [...st.events, e];
      return { events };
    }),
  selectNode: (id) => set({ selectedNode: id }),
}));
