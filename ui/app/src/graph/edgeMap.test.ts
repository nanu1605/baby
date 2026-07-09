import { describe, expect, it } from "vitest";
import { eventToActions, remapBrainId } from "./edgeMap";

describe("remapBrainId", () => {
  it("remaps backstop tier/id to brain:cloud", () => {
    expect(remapBrainId("backstop")).toBe("brain:cloud");
    expect(remapBrainId("brain:backstop")).toBe("brain:cloud");
  });
  it("prefixes bare tiers and leaves brain ids intact", () => {
    expect(remapBrainId("daily")).toBe("brain:daily");
    expect(remapBrainId("brain:nim_primary")).toBe("brain:nim_primary");
  });
  it("passes undefined through", () => {
    expect(remapBrainId(undefined)).toBeUndefined();
  });
});

describe("eventToActions — honest derivation", () => {
  it("turn_start pulses baby_core→router", () => {
    expect(eventToActions({ kind: "turn_start", source: "baby_core" })).toEqual([
      { type: "pulse", from: "baby_core", to: "router", klass: "normal" },
    ]);
  });

  it("turn_end pulses router→brain (backstop remapped to cloud)", () => {
    expect(eventToActions({ kind: "turn_end", brainTier: "backstop" })).toEqual([
      { type: "pulse", from: "router", to: "brain:cloud", klass: "normal" },
    ]);
    expect(eventToActions({ kind: "turn_end", brainTier: "nim_primary" })).toEqual([
      { type: "pulse", from: "router", to: "brain:nim_primary", klass: "normal" },
    ]);
  });

  it("router status pulses router→target brain", () => {
    expect(
      eventToActions({
        kind: "status",
        channel: "router",
        source: "router",
        target: "brain:nim_heavy",
      }),
    ).toEqual([
      { type: "pulse", from: "router", to: "brain:nim_heavy", klass: "normal" },
    ]);
  });

  it("tool_start pulses the 2-hop gate path, colored by class", () => {
    expect(
      eventToActions({
        kind: "tool_start",
        source: "brain:daily",
        target: "tool:run_shell",
        safety_class: "confirm",
      }),
    ).toEqual([
      { type: "pulse", from: "brain:daily", to: "safety_gate", klass: "confirm" },
      { type: "pulse", from: "safety_gate", to: "tool:run_shell", klass: "confirm" },
    ]);
  });

  it("tool_start with a backstop source remaps to brain:cloud", () => {
    const a = eventToActions({
      kind: "tool_start",
      source: "brain:backstop",
      target: "tool:web_search",
      safety_class: "allow",
    });
    expect(a[0]).toMatchObject({ from: "brain:cloud", to: "safety_gate", klass: "normal" });
  });

  it("tool_end flashes the tool node only on an error-ish status", () => {
    expect(
      eventToActions({ kind: "tool_end", target: "tool:run_shell", status: "error" }),
    ).toEqual([{ type: "flash", node: "tool:run_shell", klass: "error" }]);
    expect(
      eventToActions({ kind: "tool_end", target: "tool:run_shell", status: "ok" }),
    ).toEqual([]);
  });

  it("confirm_request flashes the safety gate amber", () => {
    expect(eventToActions({ kind: "confirm_request" })).toEqual([
      { type: "flash", node: "safety_gate", klass: "confirm" },
    ]);
  });

  it("voice 'heard' status pulses voice_stt→router", () => {
    expect(
      eventToActions({ kind: "status", channel: "voice", text: "voice: heard 'hi'" }),
    ).toEqual([{ type: "pulse", from: "voice_stt", to: "router", klass: "normal" }]);
  });

  it("token pulses baby_core→voice_tts on voice, nothing on ui", () => {
    expect(eventToActions({ kind: "token", channel: "voice" })).toEqual([
      { type: "pulse", from: "baby_core", to: "voice_tts", klass: "normal" },
    ]);
    expect(eventToActions({ kind: "token", channel: "ui" })).toEqual([]);
  });

  it("returns nothing for dark / zero-signal edges and unknown kinds", () => {
    // memory access, per-stage voice, and unknown kinds never pulse
    expect(eventToActions({ kind: "task_queued" })).toEqual([]);
    expect(eventToActions({ kind: "status", channel: "voice", text: "voice: listening" })).toEqual([]);
    expect(eventToActions({ kind: "turn_start", source: "somewhere_else" })).toEqual([]);
  });
});
