import { describe, expect, it } from "vitest";
import {
  flattenResults,
  pushRecent,
  resultAction,
  totalCount,
} from "./searchResults";
import type {
  ActivityResult,
  ConversationResult,
  FactResult,
  SearchResponse,
  TaskResult,
} from "../types";

const resp = (over: Partial<SearchResponse["groups"]> = {}): SearchResponse => ({
  query: "q",
  groups: { facts: [], conversations: [], activity: [], tasks: [], ...over },
});

const fact = (id: number): FactResult => ({
  type: "fact",
  id,
  snippet: `f${id}`,
  ts: null,
  node_id: "mem_facts",
});
const convo = (id: number): ConversationResult => ({
  type: "conversation",
  id,
  snippet: `c${id}`,
  ts: "2026-01-01",
  node_id: "mem_rag",
  conversation_id: 9,
});
const activity = (id: number, tool: string): ActivityResult => ({
  type: "activity",
  id,
  snippet: `${tool}: x`,
  ts: "2026-01-01",
  node_id: `tool:${tool}`,
});
const task = (id: number): TaskResult => ({
  type: "task",
  id,
  snippet: `t${id}`,
  ts: "2026-01-01",
  node_id: "task_queue",
  status: "queued",
});

describe("flattenResults", () => {
  it("orders by group (facts→conversations→activity→tasks), then server order", () => {
    const r = resp({
      tasks: [task(1)],
      facts: [fact(2), fact(3)],
      activity: [activity(4, "run_shell")],
      conversations: [convo(5)],
    });
    expect(flattenResults(r).map((f) => f.key)).toEqual([
      "facts:2",
      "facts:3",
      "conversations:5",
      "activity:4",
      "tasks:1",
    ]);
  });

  it("assigns contiguous nav indices across groups", () => {
    const r = resp({ facts: [fact(1)], tasks: [task(2), task(3)] });
    expect(flattenResults(r).map((f) => f.index)).toEqual([0, 1, 2]);
  });

  it("returns [] for a null response", () => {
    expect(flattenResults(null)).toEqual([]);
  });
});

describe("totalCount", () => {
  it("sums every group", () => {
    expect(totalCount(resp({ facts: [fact(1)], tasks: [task(2), task(3)] }))).toBe(3);
  });
  it("is 0 for empty or null", () => {
    expect(totalCount(resp())).toBe(0);
    expect(totalCount(null)).toBe(0);
  });
});

describe("resultAction", () => {
  it("fact → mem_facts + focusFact (best-effort highlight)", () => {
    expect(resultAction(fact(7))).toEqual({ nodeId: "mem_facts", focusFact: 7 });
  });
  it("conversation → mem_rag + Chat tab", () => {
    expect(resultAction(convo(1))).toEqual({ nodeId: "mem_rag", tab: "chat" });
  });
  it("activity → its tool node, no tab/focus", () => {
    expect(resultAction(activity(1, "web_search"))).toEqual({
      nodeId: "tool:web_search",
    });
  });
  it("task → task_queue, no tab/focus", () => {
    expect(resultAction(task(1))).toEqual({ nodeId: "task_queue" });
  });
});

describe("pushRecent", () => {
  it("prepends newest and caps the length", () => {
    expect(pushRecent(["a", "b", "c"], "d", 3)).toEqual(["d", "a", "b"]);
  });
  it("dedupes case-insensitively, moving the repeat to the front", () => {
    expect(pushRecent(["a", "b"], "A")).toEqual(["A", "b"]);
  });
  it("ignores blank / whitespace queries", () => {
    expect(pushRecent(["a"], "   ")).toEqual(["a"]);
  });
});
