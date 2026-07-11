import { useEffect, useRef } from "react";
import { useBrain } from "../store";
import { resumeConversationLive, returnToLive } from "../lib/historyActions";
import Message from "./Message";
import ChatInput from "./ChatInput";

/** Streaming chat transcript + composer. Auto-scrolls to the newest message.
 *  While a past chat is being VIEWED (viewingConversationId != null) the composer
 *  is replaced by a read-only banner offering "Resume here" / "Return to live". */
export default function ChatPanel() {
  const messages = useBrain((s) => s.messages);
  const chatUp = useBrain((s) => s.ws.chat);
  const viewingId = useBrain((s) => s.viewingConversationId);
  const endRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    endRef.current?.scrollIntoView({ block: "end" });
  }, [messages]);

  const onResume = () => {
    if (viewingId == null) return;
    resumeConversationLive(viewingId).then((ok) => {
      if (!ok) {
        useBrain
          .getState()
          .pushToast("Can't resume while a turn is running — try again.", "error");
      }
    });
  };

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
      {viewingId != null ? (
        <div className="viewing-bar">
          <span className="viewing-bar-label">👁 Viewing a past chat (read-only)</span>
          <div className="viewing-bar-actions">
            <button className="viewing-resume" onClick={onResume}>
              Resume here
            </button>
            <button
              className="viewing-return"
              onClick={() => returnToLive().catch(() => {})}
            >
              Return to live
            </button>
          </div>
        </div>
      ) : (
        <ChatInput />
      )}
    </div>
  );
}
