# Baby — "The Brain" UI (v3)

React 18 + TypeScript + Vite frontend. Baby's mind rendered as a living graph.

- **Dev:** `npm run dev` (Vite HMR on :5173, proxies API + WS to the live backend
  on :8765). Convenience: `scripts/dev_ui.ps1`.
- **Build:** `npm run build` → `dist/` (served by FastAPI at `/` when
  `ui.frontend: v3`). Production serving needs **no Node** — FastAPI serves the
  static `dist/`.
- **Rollback:** the vanilla UI stays at `/classic` regardless of the flag.

Sources + `package-lock.json` are committed; `node_modules/` and `dist/` are
built on setup (`scripts/setup.ps1` runs `npm ci && npm run build`).

## Stack (pinned — see package.json / package-lock.json)

- react-force-graph-2d (canvas 2D only — the GPU belongs to the LLM)
- zustand (single store: topology, live-event ring, pipeline state, selection)
- CSS-variable design tokens (`src/styles/tokens.css` — the source of truth)
