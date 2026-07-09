import { describe, expect, it } from "vitest";
import { emitAction, emitActions, subscribePulses } from "./pulseBus";
import type { PulseAction } from "./pulseBus";

describe("pulseBus", () => {
  it("delivers actions to subscribers and stops after unsubscribe", () => {
    const seen: PulseAction[] = [];
    const off = subscribePulses((a) => seen.push(a));

    emitAction({ type: "pulse", from: "a", to: "b", klass: "normal" });
    emitActions([
      { type: "flash", node: "safety_gate", klass: "confirm" },
      { type: "pulse", from: "router", to: "brain:daily", klass: "normal" },
    ]);
    expect(seen).toHaveLength(3);

    off();
    emitAction({ type: "pulse", from: "x", to: "y", klass: "error" });
    expect(seen).toHaveLength(3); // no delivery after unsubscribe
  });

  it("fans out to multiple subscribers", () => {
    let a = 0;
    let b = 0;
    const offA = subscribePulses(() => a++);
    const offB = subscribePulses(() => b++);
    emitAction({ type: "flash", node: "n", klass: "normal" });
    expect(a).toBe(1);
    expect(b).toBe(1);
    offA();
    offB();
  });
});
