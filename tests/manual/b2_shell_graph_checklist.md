# B2 manual acceptance — v3 app shell + static brain graph

Owner-run. B2 makes the v3 React UI a full daily driver at feature parity with
`/classic`, over the living-graph layout: the brain graph is the centerpiece;
chat + activity live in a collapsible right panel. **Parity gate — no animation
yet** (edge pulses / live gauge = B3; inspectors = B4; omnibox = B5). Pure
frontend: no backend/Python change. Automated coverage: `ui/app` `npm run build`
(tsc + vite) + `npm test` (vitest: store reducers, markdown sanitize, layout
math). This checklist is the live end-to-end pass.

Prereq: on branch `feature/v3-brain-ui`. `config.yaml` → `ui.frontend: v3`.
Build then start: `npm --prefix ui/app run build` → `uv run python run.py --ui` →
open `http://127.0.0.1:8765/`.

---

## §0 — Build

- [ ] `npm --prefix ui/app ci` completes; `npm --prefix ui/app audit` → **0 vulnerabilities**.
- [ ] `npm --prefix ui/app run build` → `tsc` passes, Vite writes `ui/app/dist/`.
- [ ] `npm --prefix ui/app test` → all vitest green (store + markdown + layout).
- [ ] `git status`: `ui/app/dist/` still ignored; `package-lock.json` tracked (new deps `marked`, `dompurify`, dev `vitest`, `jsdom`).

## §1 — Header parity

- [ ] Model badge shows the daily model (e.g. `qwen3.5:9b-q4_K_M`).
- [ ] Router dot: green=cloud / amber=degraded / red=offline, label matches; **stays visible in game mode**.
- [ ] State chip reflects the live pipeline (idle → thinking → speaking → executing → idle) as a turn runs — driven by `/ws/state`.
- [ ] Gauges: CPU %, RAM used/total, VRAM used/total (or `n/a` with no NVIDIA GPU). Bars recolor >65% amber / >85% red.
- [ ] Token totals `↑session.prompt ↓session.completion`; hover shows session/today + per-brain split.
- [ ] 🎮 toggle flips game mode (POST `/game_mode`), button shows `🎮 on`; router dot stays visible.
- [ ] ■ Stop (kill) cancels a running turn (POST `/kill`). `classic UI` link → `/classic`.

## §2 — Chat parity

- [ ] On load, `/history` backfills prior messages.
- [ ] Send a message → user bubble, then a streaming assistant bubble (blinking caret) that fills token by token.
- [ ] On completion the reply renders as **markdown** (headings, lists, code, links, emoji), with a **brain badge** (local/cloud/NIM/Gemini, model+reason on hover) and a **token badge** `↑prompt ↓completion`.
- [ ] Send while a turn runs → a system "Still working…" note (the `busy` frame); Send button disabled mid-turn.

## §3 — Confirmation modal (safety-gate parity)

- [ ] Ask Baby to run a gated command (e.g. a shell command that classifies **confirm**) → the amber confirm dialog opens with the command, explanation, and a live countdown.
- [ ] **Approve** → command proceeds (POST `/confirm/{id}` `{approved:true}`); **Deny**/**Esc** → refused (`{approved:false}`).
- [ ] Let the countdown expire → server auto-denies and the dialog closes (matching `confirm_resolved`). Kill switch also closes an open confirm.

## §4 — Memory dialog

- [ ] 🧠 opens the dialog; shows `N remembered · M forgotten`; lists facts (forgotten struck-through).
- [ ] ✕ on a fact deletes it (DELETE `/memory/fact/{id}`); list refreshes.
- [ ] Wipe requires typing `WIPE` in the in-dialog input; wrong text → toast "Type WIPE exactly", no wipe; correct → memory wiped (POST `/memory/wipe`), toast confirms.

## §5 — Activity panel

- [ ] Right panel **Activity** tab: each tool call is one row, left-border colored by class (allow=green / confirm=amber / deny=red), `⏳`→glyph (`✓`/`✗`/`⛔`/`🚫`) on completion, `args` in a `<details>`, result summary below.
- [ ] `status` / task / project events render as italic system lines.

## §6 — Graph canvas

- [ ] Every subsystem is present: core **Baby**, **Router**, **Safety Gate**, 3 memory, 5 voice (+ speaker verify), 5 infra, all brains, all tools. Count ≈ matches `/api/graph`.
- [ ] Type styling reads at a glance: core white/largest, brains warm, tools cyan, safety red, memory violet, voice green, infra slate, router blue.
- [ ] **Stable geography** (reload twice → same layout): voice west, brains center-east, tools east grid, memory south, infra north, core center. Edges follow real call paths.
- [ ] Pan/zoom works; hovering a node shows its label+role tooltip; clicking a node highlights it (inspector drawer is B4).
- [ ] Collapse/expand the right panel (or resize the window) → the graph **re-fits** to the new width (no crushed/off-center cluster).

## §7 — Markdown safety (defense in depth)

- [ ] A reply containing a link renders as a clickable `<a>` with `rel="noopener noreferrer"` `target="_blank"` (inspect element).
- [ ] A reply containing raw HTML / a `<script>` does **not** execute or inject — markdown-only (marked raw-HTML off) + sanitized (DOMPurify). Streaming text is never HTML.

## §8 — Perf snapshot (parity gate)

- [ ] `ui.frontend: v3`, graph rendered static (no animation), **9B model loaded**. Record idle GPU/CPU over ~30 s from the header gauges / Task Manager:
  - GPU ≈ ____ %  (budget < ~10%) · CPU ≈ ____ %  (budget < ~5%)
- [ ] Within budget → proceed to B3. Over budget → simplify node draw before animation.

## §9 — Rollback proven

- [ ] `http://127.0.0.1:8765/classic` still fully functional (chat works) while v3 is active.
- [ ] Set `ui.frontend: classic`, restart → `/` serves the vanilla UI. (Restore to `v3` after.)

## §10 — Gates

- [ ] `npm --prefix ui/app run build` + `npm --prefix ui/app test` green.
- [ ] `uv run pytest -q` green; `uv run pytest tests/test_safety.py -q` green; `uv run ruff check .` clean.
- [ ] `git branch --show-current` = `feature/v3-brain-ui`. Zero commits on master.

---

**Restore after:** set `ui.frontend` to preference. `/classic` remains available.
