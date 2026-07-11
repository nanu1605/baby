import { useCallback, useEffect, useState } from "react";
import { useBrain } from "../store";
import { getConversations } from "../api/client";
import { openConversationView, startNewChat } from "../lib/historyActions";
import type { ConversationMeta } from "../types";

/**
 * v5 chat-history sidebar — the left column of the stage. Lists real past
 * conversations (newest activity first), a prominent "New chat" button, and a
 * read-only viewer on click. The active/viewed chat is highlighted. Archived
 * chats hide behind a filter. Gated on ui.history === "on" by the caller.
 *
 * Opening a chat replaces the transcript via setTranscript (never appendToken),
 * so switching fires no phantom brain pulse.
 */
export default function HistorySidebar() {
  const activeId = useBrain((s) => s.activeConversationId);
  const viewingId = useBrain((s) => s.viewingConversationId);
  const pushToast = useBrain((s) => s.pushToast);

  const [list, setList] = useState<ConversationMeta[]>([]);
  const [showArchived, setShowArchived] = useState(false);
  const [collapsed, setCollapsed] = useState(false);

  const refresh = useCallback(async () => {
    try {
      const data = await getConversations({ includeArchived: showArchived });
      setList(data.conversations);
      useBrain.getState().setActiveConversationId(data.active_conversation_id);
    } catch {
      /* backend briefly away — next refresh recovers */
    }
  }, [showArchived]);

  // Refresh on mount, when the archived filter flips, and whenever the live
  // conversation changes (new chat / resume) so the prior chat drops into the list.
  useEffect(() => {
    refresh();
  }, [refresh, activeId]);

  const onNew = async () => {
    try {
      await startNewChat();
      await refresh();
    } catch {
      pushToast("Couldn't start a new chat.", "error");
    }
  };

  const onOpen = (id: number) => {
    openConversationView(id).catch(() =>
      pushToast("Couldn't open that conversation.", "error"),
    );
  };

  // Highlight the chat currently in the panel: the viewed one, else the live one.
  const highlightId = viewingId ?? activeId;

  if (collapsed) {
    return (
      <aside className="history-sidebar collapsed">
        <button
          className="history-expand"
          title="show chat history"
          onClick={() => setCollapsed(false)}
        >
          ☰
        </button>
      </aside>
    );
  }

  return (
    <aside className="history-sidebar">
      <div className="history-head">
        <span className="history-title">Chats</span>
        <button
          className="history-collapse"
          title="hide chat history"
          onClick={() => setCollapsed(true)}
        >
          ‹
        </button>
      </div>

      <button className="history-new" onClick={onNew} title="Start a new chat">
        ＋ New chat
      </button>

      <div className="history-list">
        {list.length === 0 ? (
          <div className="history-empty">No past chats yet.</div>
        ) : (
          list.map((c) => (
            <button
              key={c.id}
              className={"history-item" + (c.id === highlightId ? " active" : "")}
              onClick={() => onOpen(c.id)}
              title={c.title}
            >
              <span className="history-item-title">{c.title}</span>
              <span className="history-item-meta">
                <span>{relTime(c.last_message_at ?? c.started_at)}</span>
                <span className="history-item-count">{c.message_count}</span>
                {c.archived && <span className="history-item-archived">archived</span>}
              </span>
            </button>
          ))
        )}
      </div>

      <label className="history-archived-toggle">
        <input
          type="checkbox"
          checked={showArchived}
          onChange={(e) => setShowArchived(e.target.checked)}
        />
        Show archived
      </label>
    </aside>
  );
}

/** Compact relative time ("just now" / "5m" / "3h" / "2d" / a date). */
function relTime(ts: string | null): string {
  if (!ts) return "";
  const d = new Date(ts.replace(" ", "T") + (ts.includes("Z") ? "" : "Z"));
  if (isNaN(d.getTime())) return "";
  const diff = (Date.now() - d.getTime()) / 1000;
  if (diff < 60) return "just now";
  if (diff < 3600) return `${Math.floor(diff / 60)}m`;
  if (diff < 86400) return `${Math.floor(diff / 3600)}h`;
  if (diff < 86400 * 7) return `${Math.floor(diff / 86400)}d`;
  return d.toLocaleDateString(undefined, { month: "short", day: "numeric" });
}
