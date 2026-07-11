# V3 manual acceptance — the brain, in three dimensions

Owner-run, real GPU (the 5060 Ti). V3 turns the 2D living graph into a 3D neural
sphere behind `ui.brain: 3d`: honest firing along great-circle arcs, a central state
gauge whose ripple/shimmer ride real mic/TTS loudness, router-health recolor, and a
context-loss floor — all under the V2 frame governor. Backend touches are additive
only (`mic_rms`/`tts_rms` on `/ws/activity`); the router/provider/safety internals and
`tests/test_safety.py` are untouched. Automated coverage: `ui/app` `npm run build` +
`npm test` (tier gate, sphere geometry, great-circle arcs, pulse animation, amplitude
fold, node visuals, context-loss backoff) + `tests/test_amplitude.py` (RMS / quantize /
throttle / `play` on_level / activity-kind membership). This is the live end-to-end,
perf, and honest-data pass — the eyeball + measurement items automation can't cover.

Prereq: on branch `feature/v4-native-3d-brain`. `config.yaml` → `ui.brain: 3d` (the
code-default; set it explicitly to be sure). Build then start:
`npm --prefix ui/app run build` → `uv run python run.py --ui` → open
`http://127.0.0.1:8765/`. Have a GPU/CPU monitor ready (Task Manager Performance or the
browser task manager) and, for §3, a mic + `--voice`.

---

## §1 — The sphere renders (static honest topology)

- [ ] Sphere loads with `ui.brain: 3d`: real nodes sit on their group regions (voice
  west, brains equator-east, tools east, memory south, infra north, `baby_core`
  center); edges are great-circle arcs; zero-signal edges are **dark**.
- [ ] Drag-orbit works (damped, no pan, zoom clamped); idle → slow ambient
  axis-rotation only (the sole non-signal motion), quiet under ⚡ / reduced-motion.
- [ ] Click a node → the inspector drawer resolves it (topology is lifted even while
  the 2D floor is unmounted); `#node/<id>` deep-link + fly-to still work.

## §2 — Firing follows the real turn path

- [ ] Send a chat turn → a sprite travels **baby_core→router** then
  **router→brain:{tier}** along the visible arc (the same honest feed the 2D graph
  reads — both show the identical path).
- [ ] Ask Baby to run a tool → the **2-hop** fires **brain→safety_gate→tool:{name}**.
- [ ] Force a tool error → the tool node **flashes red**; a gated command →
  **safety_gate flashes amber** on `confirm_request`.
- [ ] Idle → **no** sprites (nothing faked); `brain→memory` and per-stage voice edges
  never fire (no honest signal); dark edges stay dark.

## §3 — Core state gauge + honest amplitude (voice)

- [ ] Idle: the core ring **breathes** slowly (`--state-idle`), the sole ambient motion.
- [ ] **Speak** → the gauge goes **listening** and the ring **ripple expands with your
  voice** — loud = big ring, quiet = tight ring (tracks real mic RMS, not a fixed sine).
- [ ] Thinking → blue **orbiters**; **reply** → the ring **shimmers violet with the TTS
  loudness** (bright on loud syllables); executing → sweeping spin. Matches the header
  state chip.
- [ ] Go silent → ripple/shimmer **decay to rest** within a moment (levels relax to 0,
  never stick).

## §4 — Live node states

- [ ] The brain that answered the last turn gets a brighter **active highlight**.
- [ ] Pull Wi-Fi mid-session (or force degraded/offline) → cloud brain nodes
  **recolor** (amber degraded / red offline); local `brain:daily` unaffected; the
  reroute lights **router→brain:daily** (local fallback); router dot matches.
- [ ] Toggle **game mode** (🎮) → `brain:daily` **ghosts** (dim); toggle off → it
  re-solidifies.

## §5 — Governor + bloom + VRAM (measured)

`performance_mode` **OFF** for every measurement. Force-load the local 9B for the VRAM
items (a game-mode / local turn).

- [ ] **60 fps under firing at full3d** with `performance_mode` OFF: drive a busy turn
  → sustained **≈ ____ fps** (target 60, no jank), **GPU ≈ ____ %**, **CPU ≈ ____ %**.
  If full3d can't hold 60 without `performance_mode` default-on, **STOP and escalate** —
  do not ship with perf-mode forced on.
- [ ] **Bloom quality** eyeball: bright emissive node cores glow crisply over a dark
  sphere (not a uniform grey wash). Acceptable on WebView2 in the native shell too: ___
- [ ] **VRAM delta** with the 9B loaded ≈ `____` GB; when the model is resident the
  watchdog **demotes** and **sheds** bloom + particles (full3d → lite3d), smoothly, no
  stutter; frees again after the model unloads (promote is slow/calm, no flapping).
- [ ] Force `render.tier: lite3d` → **bloom + particles off**, sphere stays; force
  `render.tier: 2d` → the 2D graph floor.

## §6 — Context-loss floor + rollback

- [ ] Force a context loss (DevTools console:
  `document.querySelector('canvas').getContext('webgl2').getExtension('WEBGL_lose_context').loseContext()`)
  → the UI **falls to the 2D graph** (never a black stage), then the sphere **remounts**
  on the backoff fuse (60 s first; a truly dead GPU backs off to 2 m → 5 m; a recovered
  one returns promptly). No hot lost-context loop, no runaway console spam.
- [ ] `config.yaml` → `ui.brain: 2d`, reload → **instantly restores the v3 canvas
  graph** (the rollback); `3d` again brings the sphere back.
- [ ] ⚡ **performance mode** / OS reduce-motion → calm: no ambient rotation, static
  gauge, particles off. Persists across reload.

## §7 — Honest-data audit

- [ ] Every directed effect rides a real signal: sprites ← the turn's own pulse feed,
  ripple ← real mic, shimmer ← real TTS, gauge ← pipeline state, recolor ← router,
  ghost ← game mode, highlight ← the answering brain.
- [ ] **Nothing fabricated:** idle = no sprites, dark edges stay dark, no timer invents
  motion, and silence decays amplitude to 0. `/classic` still works (additive keys only).

## §8 — Gates

- [ ] `npm --prefix ui/app run build` + `npm --prefix ui/app test` green.
- [ ] `uv run pytest -q` green; `uv run pytest tests/test_safety.py -q` green;
  `uv run ruff check .` clean.
- [ ] `git branch --show-current` = `feature/v4-native-3d-brain`. Zero commits on
  master.

---

**Perf numbers recorded here + in the PR.** If full3d misses 60 fps with
`performance_mode` OFF, that is a blocker for the centerpiece — escalate before V4. See
also [b3_live_graph_checklist.md](b3_live_graph_checklist.md) for the 2D floor this
sphere falls back to.
