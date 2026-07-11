import { useCallback, useEffect, useState } from "react";
import { useBrain } from "../store";
import {
  archiveConversation,
  getConversations,
  renameConversation,
} from "../api/client";
import {
  deleteConversationFlow,
  openConversationView,
  startNewChat,
} from "../lib/historyActions";
import type { ConversationMeta } from "../types";

/**
 * v5 chat-history sidebar — the left column of the stage. Lists real past
 * conversations (newest activity first), a prominent "New chat" button, a
 * read-only viewer on click, and per-row rename / archive / delete. The
 * active/viewed chat is highlighted; archived chats hide behind a filter.
 * Gated on ui.history === "on" by the caller.
 *
 * Opening/switching a chat replaces the transcript via setTranscript (never
 * appendToken), so it fires no phantom brain pulse.
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
  // conversation changes (new chat / resume / delete) so the list stays honest.
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
            <HistoryRow
              key={c.id}
              conv={c}
              active={c.id === highlightId}
              onRefresh={refresh}
            />
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

function HistoryRow({
  conv,
  active,
  onRefresh,
}: {
  conv: ConversationMeta;
  active: boolean;
  onRefresh: () => Promise<void>;
}) {
  const pushToast = useBrain((s) => s.pushToast);
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(conv.title);

  const open = () =>
    openConversationView(conv.id).catch(() =>
      pushToast("Couldn't open that conversation.", "error"),
    );

  const commitRename = async () => {
    setEditing(false);
    const title = draft.trim();
    if (!title || title === conv.title) return;
    try {
      await renameConversation(conv.id, title);
      await onRefresh();
    } catch {
      pushToast("Rename failed.", "error");
    }
  };

  const onArchive = async () => {
    try {
      await archiveConversation(conv.id, !conv.archived);
      await onRefresh();
    } catch {
      pushToast("Archive failed.", "error");
    }
  };

  const onDelete = async () => {
    if (!window.confirm("Delete this chat permanently? It's removed from search too."))
      return;
    const ok = await deleteConversationFlow(conv.id);
    if (!ok) {
      pushToast("Can't delete the active chat while a turn is running.", "error");
      return;
    }
    await onRefresh();
  };

  if (editing) {
    return (
      <div className="history-item editing">
        <input
          className="history-rename-input"
          value={draft}
          autoFocus
          onChange={(e) => setDraft(e.target.value)}
          onBlur={commitRename}
          onKeyDown={(e) => {
            if (e.key === "Enter") commitRename();
            else if (e.key === "Escape") {
              setDraft(conv.title);
              setEditing(false);
            }
          }}
        />
      </div>
    );
  }

  return (
    <div className={"history-item" + (active ? " active" : "")}>
      <button className="history-item-open" onClick={open} title={conv.title}>
        <span className="history-item-title">{conv.title}</span>
        <span className="history-item-meta">
          <span>{relTime(conv.last_message_at ?? conv.started_at)}</span>
          <span className="history-item-count">{conv.message_count}</span>
          {conv.archived && <span className="history-item-archived">archived</span>}
        </span>
      </button>
      <div className="history-item-actions">
        <button
          title="Rename"
          onClick={() => {
            setDraft(conv.title);
            setEditing(true);
          }}
        >
          ✎
        </button>
        <button title={conv.archived ? "Unarchive" : "Archive"} onClick={onArchive}>
          {conv.archived ? "⤴" : "🗄"}
        </button>
        <button title="Delete" onClick={onDelete}>
          🗑
        </button>
      </div>
    </div>
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
