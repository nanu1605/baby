import { useState } from "react";
import { sendUserMessage } from "../hooks/useChatSocket";
import { useBrain } from "../store";

/** Chat composer. Adds the user bubble locally, then sends over /ws/chat. Hosts
 *  the one-turn best-brain boost (⚡) + its armed chip. */
export default function ChatInput() {
  const [text, setText] = useState("");
  const running = useBrain((s) => s.pipeline !== "idle");
  const boostArmed = useBrain((s) => s.boostArmed);

  const submit = (e: React.FormEvent) => {
    e.preventDefault();
    const t = text.trim();
    if (!t) return;
    useBrain.getState().addUserMessage(t);
    sendUserMessage(t);
    setText("");
  };

  const toggleBoost = () =>
    boostArmed ? useBrain.getState().disarmBoost() : useBrain.getState().armBoost();

  return (
    <div className="chat-composer">
      {boostArmed && (
        <div className="boost-chip">
          <span>⚡ boost armed — next turn prefers the strongest brain</span>
          <button
            className="boost-cancel"
            title="cancel boost"
            onClick={() => useBrain.getState().disarmBoost()}
          >
            ✕
          </button>
        </div>
      )}
      <form className="chat-form" onSubmit={submit}>
        <button
          type="button"
          className={"boost-icon" + (boostArmed ? " on" : "")}
          title="Prefer the strongest brain for the next turn"
          onClick={toggleBoost}
        >
          ⚡
        </button>
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
    </div>
  );
}
