import { useBrain } from "./store";

/**
 * B0 shell — "hello brain". Just enough to prove the v3 frontend builds,
 * serves at `/`, and the dark design-token surface renders. The chat panel,
 * confirmation modal, and the living graph land in B2/B3.
 */
export default function App() {
  const pipeline = useBrain((s) => s.pipeline);

  return (
    <div className="app-shell">
      <header className="topbar">
        <span className="brand">Baby</span>
        <span className="brand-sub">The Brain</span>
        <span className="build-tag">v3 · shell</span>
        <a className="classic-link" href="/classic">
          classic&nbsp;UI
        </a>
      </header>

      <main className="stage">
        <div className="hello" data-state={pipeline}>
          <div className="core-orb" aria-hidden="true" />
          <h1>hello brain</h1>
          <p className="muted">
            v3 shell is live. The canvas graph, chat, and inspectors arrive in
            B2–B4.
          </p>
        </div>
      </main>
    </div>
  );
}
