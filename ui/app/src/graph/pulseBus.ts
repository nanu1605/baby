/**
 * Pulse bus — a tiny module-level pub/sub for graph animation actions (B3).
 *
 * Deliberately OUTSIDE zustand: pulses fire at token rate; routing them through
 * the React store would trigger a re-render per token and blow the perf budget.
 * Hooks `emit(...)` honest actions; BrainGraph subscribes, coalesces, and paints.
 */

export type PulseClass = "normal" | "confirm" | "error";

/** An edge particle (from→to) or a transient node flash. */
export type PulseAction =
  | { type: "pulse"; from: string; to: string; klass: PulseClass }
  | { type: "flash"; node: string; klass: PulseClass };

type Handler = (a: PulseAction) => void;

const handlers = new Set<Handler>();

export function subscribePulses(h: Handler): () => void {
  handlers.add(h);
  return () => {
    handlers.delete(h);
  };
}

export function emitAction(a: PulseAction): void {
  for (const h of handlers) h(a);
}

export function emitActions(actions: PulseAction[]): void {
  for (const a of actions) emitAction(a);
}
