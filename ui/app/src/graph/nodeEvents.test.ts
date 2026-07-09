import { describe, expect, it } from "vitest";
import { nodeEvents } from "./nodeEvents";
import type { LiveEvent } from "../types";

const ev = (over: Partial<LiveEvent>): LiveEvent => ({
  seq: 0,
  kind: "status",
  channel: "ui",
  ts: "",
  payload: {},
  ...over,
});

describe("nodeEvents", () => {
  const events: LiveEvent[] = [
    ev({ seq: 1, kind: "turn_start", source: "baby_core" }),
    ev({ seq: 2, kind: "tool_start", source: "brain:daily", target: "tool:run_shell", payload: { tool: "run_shell" } }),
    ev({ seq: 3, kind: "tool_end", payload: { tool: "run_shell", status: "ok" } }),
    ev({ seq: 4, kind: "status", source: "router", target: "brain:cloud" }),
  ];

  it("matches by source or target node id", () => {
    expect(nodeEvents(events, "baby_core").map((e) => e.seq)).toEqual([1]);
    expect(nodeEvents(events, "brain:cloud").map((e) => e.seq)).toEqual([4]);
    expect(nodeEvents(events, "brain:daily").map((e) => e.seq)).toEqual([2]);
  });

  it("matches a tool node by source/target AND payload.tool", () => {
    // tool:run_shell → the tool_start (target) + tool_end (payload.tool)
    expect(nodeEvents(events, "tool:run_shell").map((e) => e.seq)).toEqual([2, 3]);
  });

  it("returns [] for a node with no activity", () => {
    expect(nodeEvents(events, "telegram")).toEqual([]);
  });

  it("caps to the most recent `limit`", () => {
    const many: LiveEvent[] = Array.from({ length: 50 }, (_, i) =>
      ev({ seq: i, source: "baby_core" }),
    );
    const got = nodeEvents(many, "baby_core", 5);
    expect(got).toHaveLength(5);
    expect(got.map((e) => e.seq)).toEqual([45, 46, 47, 48, 49]);
  });
});
