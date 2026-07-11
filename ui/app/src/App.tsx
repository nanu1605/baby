import { useBrain } from "./store";
import { useChatSocket } from "./hooks/useChatSocket";
import { useActivitySocket } from "./hooks/useActivitySocket";
import { useStateSocket } from "./hooks/useStateSocket";
import { useStats } from "./hooks/useStats";
import { useDeepLink } from "./hooks/useDeepLink";
import { useGovernor } from "./graph/governor/useGovernor";
import { useMotionFlag } from "./hooks/useMotionFlag";
import { Component, lazy, Suspense, type ReactNode } from "react";
import Header from "./components/Header";
import BrainGraph from "./components/BrainGraph";

// Lazy: three loads only with BrainSphere, keeping it out of the entry bundle (V3a).
// A ui.brain:2d box settles on BrainGraph once the first /stats resolves the flag
// (the initial frame may briefly mount the sphere); BrainGraph is the Suspense
// fallback so the swap is seamless.
const BrainSphere = lazy(() => import("./components/BrainSphere"));

/**
 * Floor-of-last-resort for the 3D branch: if three throws synchronously (e.g. the
 * browser blocklisted WebGL after repeated GPU resets), render the 2D graph instead
 * of white-screening the whole app, and arm the store's one-shot 60 s retry. During
 * the lockout App renders the BrainGraph branch directly, so this boundary unmounts
 * and comes back fresh (un-failed) for the retry.
 */
class SphereBoundary extends Component<{ children: ReactNode }, { failed: boolean }> {
  state = { failed: false };
  static getDerivedStateFromError() {
    return { failed: true };
  }
  componentDidCatch() {
    useBrain.getState().setContextLost(true);
  }
  render() {
    return this.state.failed ? <BrainGraph /> : this.props.children;
  }
}
import ChatPanel from "./components/ChatPanel";
import HistorySidebar from "./components/HistorySidebar";
import ActivityPanel from "./components/ActivityPanel";
import ConfirmModal from "./components/ConfirmModal";
import MemoryDialog from "./components/MemoryDialog";
import InspectorDrawer from "./components/InspectorDrawer";
import Omnibox from "./components/Omnibox";
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
  useDeepLink();
  useGovernor();
  useMotionFlag();

  const tab = useBrain((s) => s.rightTab);
  const collapsed = useBrain((s) => s.rightCollapsed);
  const renderTier = useBrain((s) => s.renderTier);
  const contextLost = useBrain((s) => s.contextLost);
  // v5 history sidebar: shown unless ui.history is explicitly "off" (code-default
  // "on"). Undefined before the first /stats resolves → shown, matching the default.
  const historyOn = useBrain((s) => s.stats?.ui?.history) !== "off";

  return (
    <div className="app-shell">
      <Header />

      <main className="stage">
        {historyOn && <HistorySidebar />}

        {renderTier === "2d" || contextLost ? (
          <BrainGraph />
        ) : (
          <SphereBoundary>
            <Suspense fallback={<BrainGraph />}>
              <BrainSphere />
            </Suspense>
          </SphereBoundary>
        )}

        {collapsed ? (
          <button
            className="panel-expand"
            onClick={() => useBrain.getState().toggleRightPanel()}
            title="show panel"
          >
            ‹
          </button>
        ) : (
          <>
            {/* Mobile only (CSS-gated): tap-away closes the slide-over panel. */}
            <div
              className="panel-backdrop"
              onClick={() => useBrain.getState().toggleRightPanel()}
            />
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
          </>
        )}
      </main>

      <Omnibox />
      <InspectorDrawer />
      <ConfirmModal />
      <MemoryDialog />
      <Toasts />
    </div>
  );
}
