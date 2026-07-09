import { useEffect, useRef } from "react";
import { useBrain } from "../store";
import Message from "./Message";
import ChatInput from "./ChatInput";

/** Streaming chat transcript + composer. Auto-scrolls to the newest message. */
export default function ChatPanel() {
  const messages = useBrain((s) => s.messages);
  const chatUp = useBrain((s) => s.ws.chat);
  const endRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    endRef.current?.scrollIntoView({ block: "end" });
  }, [messages]);

  return (
    <div className="chat-panel">
      <div className="messages">
        {messages.length === 0 && (
          <div className="empty-hint">
            {chatUp
              ? "Say hi to Baby to start a conversation."
              : "Backend unreachable — reconnecting…"}
          </div>
        )}
        {messages.map((m, i) => (
          <Message key={i} m={m} />
        ))}
        <div ref={endRef} />
      </div>
      <ChatInput />
    </div>
  );
}
