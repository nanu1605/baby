import { useBrain } from "./store";
import { useChatSocket } from "./hooks/useChatSocket";
import { useActivitySocket } from "./hooks/useActivitySocket";
import { useStateSocket } from "./hooks/useStateSocket";
import { useStats } from "./hooks/useStats";
import Header from "./components/Header";
import BrainGraph from "./components/BrainGraph";
import ChatPanel from "./components/ChatPanel";
import ActivityPanel from "./components/ActivityPanel";
import ConfirmModal from "./components/ConfirmModal";
import MemoryDialog from "./components/MemoryDialog";
import Toasts from "./components/Toasts";

/**
 * B2 app shell — daily-driver parity with /classic, over the living-graph layout:
 * the brain graph is the centerpiece; chat + activity live in a collapsible right
 * panel. All four data channels are wired once here.
 */
export default function App() {
  useChatSocket();
  useActivitySocket();
  useStateSocket();
  useStats();

  const tab = useBrain((s) => s.rightTab);
  const collapsed = useBrain((s) => s.rightCollapsed);

  return (
    <div className="app-shell">
      <Header />

      <main className="stage">
        <BrainGraph />

        {collapsed ? (
          <button
            className="panel-expand"
            onClick={() => useBrain.getState().toggleRightPanel()}
            title="show panel"
          >
            ‹
          </button>
        ) : (
          <aside className="side-panel">
            <div className="tabs">
              <button
                className={tab === "chat" ? "active" : ""}
                onClick={() => useBrain.getState().setTab("chat")}
              >
                Chat
              </button>
              <button
                className={tab === "activity" ? "active" : ""}
                onClick={() => useBrain.getState().setTab("activity")}
              >
                Activity
              </button>
              <button
                className="panel-collapse"
                onClick={() => useBrain.getState().toggleRightPanel()}
                title="hide panel"
              >
                ›
              </button>
            </div>
            {tab === "chat" ? <ChatPanel /> : <ActivityPanel />}
          </aside>
        )}
      </main>

      <ConfirmModal />
      <MemoryDialog />
      <Toasts />
    </div>
  );
}
