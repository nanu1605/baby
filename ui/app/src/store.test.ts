import { beforeEach, describe, expect, it } from "vitest";
import { useBrain } from "./store";
import { renderMarkdown } from "./lib/markdown";

const reset = () => useBrain.setState({ messages: [] });

describe("chat stream reducers", () => {
  beforeEach(reset);

  it("appendToken concatenates into the open streaming bubble", () => {
    const b = useBrain.getState();
    b.startTurn();
    b.appendToken("Hel");
    b.appendToken("lo");
    const msgs = useBrain.getState().messages;
    expect(msgs).toHaveLength(1);
    expect(msgs[0]).toMatchObject({
      role: "assistant",
      text: "Hello",
      streaming: true,
    });
  });

  it("a token racing ahead of turn_start still opens a bubble", () => {
    useBrain.getState().appendToken("hi");
    expect(useBrain.getState().messages[0]).toMatchObject({
      role: "assistant",
      streaming: true,
      text: "hi",
    });
  });

  it("finishTurn swaps streamed text for the authoritative reply + badges", () => {
    const b = useBrain.getState();
    b.startTurn();
    b.appendToken("raw <think>leak</think> partial");
    b.finishTurn({
      reply: "clean answer",
      brain: { tier: "daily", model: "qwen" },
      tokens: { prompt: 1, completion: 2, total: 3 },
    });
    const m = useBrain.getState().messages[0];
    expect(m.streaming).toBe(false);
    expect(m.text).toBe("clean answer");
    expect(m.brain?.tier).toBe("daily");
    expect(m.tokens?.total).toBe(3);
  });

  it("finishTurn falls back to '…' when reply and stream are both empty", () => {
    const b = useBrain.getState();
    b.startTurn();
    b.finishTurn({});
    expect(useBrain.getState().messages[0].text).toBe("…");
  });

  it("addSystemNote appends a system message (the busy line)", () => {
    useBrain.getState().addSystemNote("Still working…");
    const last = useBrain.getState().messages.at(-1);
    expect(last).toMatchObject({ role: "system", text: "Still working…" });
  });
});

describe("plain-stream → sanitized-markdown swap (turn_end)", () => {
  beforeEach(reset);

  it("finalizes as a non-streaming reply that renders to safe markdown", () => {
    const b = useBrain.getState();
    b.startTurn();
    b.appendToken("streaming plain text");
    b.finishTurn({
      reply: "# Title\n\n[link](https://example.com)\n\n<script>alert(1)</script>",
    });
    const m = useBrain.getState().messages[0];
    expect(m.streaming).toBe(false); // no longer plain-streamed
    const html = renderMarkdown(m.text); // the render path Message uses
    expect(html).toContain("<h1>");
    expect(html).not.toContain("<script>");
    expect(html).toContain('rel="noopener noreferrer"');
  });
});
