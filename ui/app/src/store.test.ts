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

describe("v5 view-only gate (viewingConversationId)", () => {
  beforeEach(() =>
    useBrain.setState({ messages: [], viewingConversationId: null, activeConversationId: null }),
  );

  it("setTranscript replaces the whole transcript", () => {
    const b = useBrain.getState();
    b.addUserMessage("live one");
    b.setTranscript([
      { role: "user", text: "past q" },
      { role: "assistant", text: "past a" },
    ]);
    expect(useBrain.getState().messages).toEqual([
      { role: "user", text: "past q" },
      { role: "assistant", text: "past a" },
    ]);
  });

  it("streaming reducers no-op while a past chat is being viewed", () => {
    const b = useBrain.getState();
    b.setTranscript([{ role: "user", text: "frozen" }]);
    b.setViewing(42);
    // A live turn arrives while viewing — must not touch the frozen transcript.
    b.startTurn();
    b.appendToken("live token");
    b.finishTurn({ reply: "live reply" });
    b.addUserMessage("typed while viewing");
    b.addSystemNote("busy");
    expect(useBrain.getState().messages).toEqual([{ role: "user", text: "frozen" }]);
  });

  it("returns to live streaming once viewing clears", () => {
    const b = useBrain.getState();
    b.setViewing(7);
    b.startTurn(); // ignored
    b.setViewing(null);
    b.startTurn();
    b.appendToken("hi");
    const msgs = useBrain.getState().messages;
    expect(msgs).toHaveLength(1);
    expect(msgs[0]).toMatchObject({ role: "assistant", text: "hi", streaming: true });
  });
});

describe("long-session hygiene caps (B7)", () => {
  beforeEach(() => useBrain.setState({ messages: [], toasts: [] }));

  it("capMessages keeps the last 300, dropping the oldest and keeping the newest", () => {
    const b = useBrain.getState();
    for (let i = 0; i < 350; i++) b.addUserMessage(`m${i}`);
    const msgs = useBrain.getState().messages;
    expect(msgs).toHaveLength(300);
    expect(msgs.at(-1)?.text).toBe("m349");
    expect(msgs[0].text).toBe("m50");
  });

  it("front-trim never drops a still-streaming tail bubble", () => {
    const b = useBrain.getState();
    for (let i = 0; i < 320; i++) b.addUserMessage(`m${i}`);
    b.startTurn(); // the streaming bubble is now the tail
    b.appendToken("live");
    const msgs = useBrain.getState().messages;
    expect(msgs).toHaveLength(300);
    expect(msgs.at(-1)).toMatchObject({ streaming: true, text: "live" });
  });

  it("pushToast caps the toast stack, keeping the newest", () => {
    const b = useBrain.getState();
    for (let i = 0; i < 8; i++) b.pushToast(`t${i}`);
    const toasts = useBrain.getState().toasts;
    expect(toasts).toHaveLength(5);
    expect(toasts.at(-1)?.text).toBe("t7");
    expect(toasts[0].text).toBe("t3");
  });
});

describe("WS resilience reducers (B7)", () => {
  beforeEach(() => useBrain.setState({ messages: [], ws: { chat: false, activity: false, state: false } }));

  it("setWsStatus tracks per-channel liveness", () => {
    const b = useBrain.getState();
    b.setWsStatus("chat", true);
    b.setWsStatus("state", true);
    const ws = useBrain.getState().ws;
    expect(ws).toEqual({ chat: true, activity: false, state: true });
  });

  it("interruptTurn finalizes a mid-stream bubble and notes the drop", () => {
    const b = useBrain.getState();
    b.startTurn();
    b.appendToken("half a sen");
    b.interruptTurn();
    const msgs = useBrain.getState().messages;
    const asst = msgs.find((m) => m.role === "assistant");
    expect(asst).toMatchObject({ streaming: false, text: "half a sen" });
    expect(msgs.at(-1)).toMatchObject({ role: "system" });
  });

  it("interruptTurn is a no-op when nothing is streaming", () => {
    useBrain.setState({ messages: [{ role: "user", text: "hi" }] });
    useBrain.getState().interruptTurn();
    expect(useBrain.getState().messages).toHaveLength(1);
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
