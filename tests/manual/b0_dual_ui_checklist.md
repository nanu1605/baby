# B0 manual acceptance — dual-UI flag & v3 scaffold

Owner-run. B0 ships the v3 frontend scaffold + the `ui.frontend` flag; no graph
yet ("hello brain" shell). Automated coverage: `tests/test_ui.py`
(`test_default_frontend_is_classic`, `test_classic_route_always_served`,
`test_v3_frontend_served_when_built`, `test_v3_flag_falls_back_to_classic_when_unbuilt`).
This checklist is the live end-to-end pass.

Prereq: on branch `feature/v3-brain-ui`. Node LTS installed (`node --version`).

---

## §0 — Build the frontend

- [ ] `npm --prefix ui/app ci` completes; `npm --prefix ui/app audit` → **0 vulnerabilities**.
- [ ] `npm --prefix ui/app run build` → `tsc` passes, Vite writes `ui/app/dist/index.html` + `ui/app/dist/assets/`.
- [ ] `git status` shows `ui/app/dist/` and `ui/app/node_modules/` **untracked/ignored** (not staged); `ui/app/package-lock.json` **is** tracked.

## §1 — Classic default (rollback baseline)

- [ ] `config.yaml` → `ui.frontend: classic` (default). Start: `uv run python run.py --ui`.
- [ ] `http://127.0.0.1:8765/` → the vanilla UI (chat + activity feed + gauges). Page source references `/static/app.js`.
- [ ] `http://127.0.0.1:8765/classic` → identical vanilla UI.
- [ ] Send one chat message → normal reply (daily-driver still works). Stop the server.

## §2 — Flip to v3

- [ ] `config.yaml` → `ui.frontend: v3`. Restart `uv run python run.py --ui`.
- [ ] `http://127.0.0.1:8765/` → dark "hello brain" shell (core orb breathing, `v3 · shell` tag, `classic UI` link top-right).
- [ ] Browser devtools Network: `/assets/index-*.js` + `/assets/index-*.css` load 200. No 404s, no console errors.
- [ ] Click the header `classic UI` link → lands on `/classic`, vanilla UI fully functional (chat works). **Rollback path proven while v3 is active.**

## §3 — Unbuilt-v3 graceful fallback

- [ ] Stop server. Rename/remove `ui/app/dist/` (simulate a fresh checkout that skipped the build).
- [ ] `ui.frontend: v3` still set. Restart `--ui`.
- [ ] `http://127.0.0.1:8765/` → serves the **classic** UI (not a crash / not a blank page). Server log shows the warning: `ui.frontend=v3 but ui/app/dist is not built; serving classic UI.`
- [ ] Rebuild (`npm --prefix ui/app run build`) → restart → `/` serves v3 again.

## §4 — Production-serving needs no Node

- [ ] With `dist/` built and `ui.frontend: v3`, the running server serves `/` from static files only — no `node`/`vite` process is running (Task Manager / `Get-Process node` empty). Confirms Node is build-time only.

## §5 — Dev proxy (optional, for frontend iteration)

- [ ] Backend running (`uv run python run.py --ui`). In another terminal: `scripts\dev_ui.ps1`.
- [ ] `http://127.0.0.1:5173` → v3 shell with HMR; editing `ui/app/src/App.tsx` hot-reloads.
- [ ] `/stats` and `/ws/*` proxy through to the live backend (devtools shows requests hitting :8765 data).

## §6 — Gates

- [ ] `uv run pytest -q` green; `uv run pytest tests/test_safety.py -q` green; `uv run ruff check .` clean.
- [ ] `git branch --show-current` = `feature/v3-brain-ui`. Zero commits on master.

---

**Restore after:** set `ui.frontend` back to your preference (`classic` until B2 parity is reached). `/classic` remains available regardless.
