# B3 manual acceptance — the graph comes alive

Owner-run. B3 adds the honest animation layer on B2's static graph: edge
particles fired from real events, the central Baby-core **status gauge**, live
node states (router-health recolor, active-brain highlight, game-mode ghost), and
an **idle-throttled render clock**. Pure frontend (backend spine + attribution
shipped in B1). Automated coverage: `ui/app` `npm run build` + `npm test`
(edge-derivation map incl. `backstop→cloud`, dark edges; pulse bus). This is the
live end-to-end + perf pass.

Prereq: on branch `feature/v3-brain-ui`. `config.yaml` → `ui.frontend: v3`. Build
then start: `npm --prefix ui/app run build` → `uv run python run.py --ui` → open
`http://127.0.0.1:8765/`. Have a browser GPU/CPU monitor ready (Task Manager
Performance, or the browser task manager).

---

## §1 — Edge pulses follow the real turn path

- [ ] Send a chat turn → watch a particle travel **baby_core→router** (turn start), then **router→brain:{tier}** (the brain that answered; `nim_primary`/`gpt-4o-mini` shows even though it emits no decision event — derived from `turn_end.brain`).
- [ ] Ask Baby to run a tool → particles travel **brain→safety_gate→tool:{name}** (2-hop through the gate).
- [ ] `backstop`/Gemini turn (force a cloud failover if you can) lights **router→brain:cloud** (not a missing `brain:backstop`).
- [ ] Idle → no particles (nothing faked). `brain→memory` and per-stage voice `wake`/`vad` never pulse (no honest signal).

## §2 — Pulse color by class + node flash

- [ ] An **allow** tool pulses normal (blue `--pulse-normal`); a **confirm**-class tool pulses amber; a **deny** pulses red.
- [ ] A gated command → **safety_gate flashes amber** on `confirm_request`.
- [ ] Force a tool error (e.g. a shell command that fails) → the **tool node flashes red** on `tool_end` error.

## §3 — Central status gauge (owner centerpiece)

- [ ] Idle: the Baby-core node **breathes** (slow scale pulse), `--state-idle`.
- [ ] During a turn the gauge shows the state: **thinking** (orbiting motes) → **speaking** (shimmer) → **executing** (spinning arc) while a tool runs → back to idle. Matches the header state chip.
- [ ] Voice turn: **listening** ring while it captures speech.

## §4 — Live node states

- [ ] The brain that answered the last turn gets an **active highlight** ring.
- [ ] Pull Wi-Fi mid-session (or force degraded/offline) → cloud brain nodes **recolor** (amber degraded / red offline); local `brain:daily` unaffected; router dot matches.
- [ ] Toggle **game mode** (🎮) → `brain:daily` **ghosts** (dim, dashed); toggle off → it re-solidifies.

## §5 — Idle-throttled render clock + perf gate (measured)

`performance_mode` **OFF** for both measurements. 9B model loaded.

- [ ] **Idle throttled** (gauge breathing, no traffic): sustained **GPU ≈ ____ %** (budget < ~10%), **CPU ≈ ____ %** (budget < ~5%). The canvas should be repainting at ~20–24fps, not 60.
- [ ] **Live turn pulsing**: during an active turn with particles, **GPU ≈ ____ %**, **CPU ≈ ____ %**, still within budget, ~60fps, no jank.
- [ ] **Both must clear with `performance_mode` OFF.** If idle-throttled misses budget, STOP and report — do not just leave `performance_mode` on.
- [ ] Switch to another tab (graph hidden) → the render loop **hard-pauses** (CPU for the tab drops ~to zero); return → it resumes.

## §6 — Perf mode + reduced motion

- [ ] Toggle ⚡ **performance mode** → particles stop, the core shows a static state color, and the canvas goes quiet when idle (no repaint at rest). Setting persists across reload (localStorage).
- [ ] OS "reduce motion" on → same calm behavior automatically (no breathing animation, no particles).

## §7 — /classic unaffected

- [ ] `http://127.0.0.1:8765/classic` still works (the enriched events carry only additive keys the vanilla UI ignores).

## §8 — Gates

- [ ] `npm --prefix ui/app run build` + `npm --prefix ui/app test` green.
- [ ] `uv run pytest -q` green; `tests/test_safety.py` green; `uv run ruff check .` clean.
- [ ] `git branch --show-current` = `feature/v3-brain-ui`. Zero commits on master.

---

**Perf numbers recorded here + in the PR.** If the budget is missed with
`performance_mode` OFF, that is a blocker for the centerpiece — escalate before B4.
