import { useState } from "react";
import { sendUserMessage } from "../hooks/useChatSocket";
import { useBrain } from "../store";

/** Chat composer. Adds the user bubble locally, then sends over /ws/chat. */
export default function ChatInput() {
  const [text, setText] = useState("");
  const running = useBrain((s) => s.pipeline !== "idle");

  const submit = (e: React.FormEvent) => {
    e.preventDefault();
    const t = text.trim();
    if (!t) return;
    useBrain.getState().addUserMessage(t);
    sendUserMessage(t);
    setText("");
  };

  return (
    <form className="chat-form" onSubmit={submit}>
      <input
        className="chat-input"
        value={text}
        onChange={(e) => setText(e.target.value)}
        placeholder="Message Baby…"
        autoComplete="off"
      />
      <button className="send-btn" type="submit" disabled={running}>
        Send
      </button>
    </form>
  );
}
