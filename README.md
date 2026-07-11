# Baby

A Jarvis-style, voice-enabled personal AI assistant for Windows 11.
**Cloud-primary brains, local-first guarantees**: fast cloud models serve by
default, but privacy-pinned turns never leave the PC, and the warm local 9B
keeps everything working with the Wi-Fi cable pulled.

> Status: **v4.0.0 — native app + 3D neural brain** ✅ — a thin Tauri desktop shell,
> the living graph reborn as a 3D neural sphere, and a coherent motion system, all held
> to 60 fps by a frame governor. The browser UI and the v3 2D graph stay one flag away.
> Full build plan: [BABY_PROJECT_PLAN.md](BABY_PROJECT_PLAN.md) ·
> change spec: [NIM_MIGRATION_PLAN.md](NIM_MIGRATION_PLAN.md)

## What works right now

- **Projects (multi-agent)**: "start a project: build me a starter FastAPI
  app with auth and tests" → the smartest available brain plans it into
  independent subtasks, background workers execute them in parallel (each
  its own agent, every tool still gated), progress streams per worker, and
  the integrated result is announced. `GET /projects` shows the board.
- **Screen awareness**: "what's on my screen?" — typed or spoken — is
  answered by the same local model that's already loaded (zero VRAM cost);
  Gemini vision steps in only if the local path fails, and the feed says
  so out loud. `screen.allow_cloud_fallback: false` keeps screenshots
  on-machine, always.
- **Speaker verification v2** (ships off by default): enroll natural speech
  across a few mic positions (`scripts\enroll_voice_v2.py`) — a profile is a
  *set* of centroids scored by max-cosine, not v1's single mean that
  false-rejected natural speech. Per-utterance scores feed a **session-trust
  smoother** (optimistic-demote): a fresh session starts trusted and drops to
  chat-only only on a *sustained* confident non-owner score, so one borderline
  utterance never locks you out. Feeds the existing binary safety-gate hook — no
  gate logic changed. `mode: observe` scores + logs every utterance without
  enforcing (the soak data-collection mode); `scripts\speaker_report.py` turns
  those logs into a per-model FAR/FRR report. Flip on only if the numbers clear
  (owner FRR ≤2% AND 0 non-owner accepted). "baby stop" works for any voice;
  PTT is always trusted.
- **Game mode**: fullscreen app detected (or "game mode on") → the local 9B
  unloads, VRAM goes to your game, and cloud brains carry every turn. Even
  privacy-pinned turns stay honest: they load the local model just for that
  answer and evict it immediately. Alt-tab out and Baby re-warms itself.
- **Phone access**: [docs/TAILSCALE.md](docs/TAILSCALE.md) — the UI on your
  phone over a private tailnet, HTTPS, zero exposed ports.
- **Background tasks**: "in the background, research the top 3 EVs under
  15 lakh" → Baby hands back a task id and keeps chatting; when the task
  finishes you get a toast, a spoken announcement, and (if enabled) a
  Telegram message. `task_status` / `cancel_task` / `GET /tasks` to manage.
- **Four brains, cloud-primary**: openai/gpt-4o-mini via OpenRouter serves
  interactive turns (first token ~1.2 s); "use the big brain" (or a planning
  task) escalates to z-ai/glm-5.2 on NVIDIA NIM; Gemini's free tier backstops;
  the local 9B stays warm for offline, privacy-pinned and overflow turns. The
  activity feed narrates every routing decision; the header badge shows which
  brain answered.
- **Token telemetry**: every answer carries its cost — `↑prompt ↓completion`
  beside the brain badge (local turns tagged "no quota"), and the header shows
  session + today totals with a per-brain split. Captured straight from each
  provider's `usage` (NIM/OpenRouter, Gemini, Ollama) into a `usage_log` table
  keyed by turn; `telemetry.emit_usage: false` opts a stubborn host out.
- **Browser**: "open ollama.com and read me the headline" drives a real
  Chromium window with a persistent profile (logins survive restarts).
  Navigation and reading are free; the FIRST click/type on each site asks
  you once per session.
- **Morning briefing**: 08:00 (or within an hour of waking the PC) Baby
  speaks the date, Indore weather, pending tasks, headlines, and system
  health. Cron-able one-liners live in the `schedules` table.
- **Telegram**: message Baby from your phone — replies, task notifications,
  and even confirmation buttons (✅/❌) for gated actions. Locked to YOUR
  chat id; everyone else gets silence.
- **Autostart**: `scripts\autostart.ps1` registers a hidden logon task —
  Baby is ready with the spoken cue shortly after you reach the desktop.
  `-Remove` to undo.
- **Tray icon**: a status dot in the system tray — green (ready), amber
  (working), red (waiting for your confirmation). Right-click: Open Baby /
  Quit Baby.
- **Voice** (`run.py --voice`): say **"hey jarvis"** (interim — train your
  own "hey baby" via [scripts/wakeword_training.md](scripts/wakeword_training.md)),
  hear a beep, speak; Baby transcribes locally (Whisper large-v3-turbo, CPU),
  runs the same agent + safety gate as text, and talks back (Kokoro TTS —
  Hindi sentences get a Hindi voice). Talk over Baby to interrupt; say
  **"baby stop"** / **"baby ruk ja"** to kill the turn; **ctrl+alt+b** is
  push-to-talk. Gated actions are never confirmed by voice — Baby says
  "check the screen" and the modal appears in the web UI. Voice adds zero
  VRAM: everything but the LLM runs on CPU.
- **Long-term memory**: "Remember my gym days are Mon/Wed/Fri" survives
  restarts — facts live in `baby.db` as e5 embeddings (sqlite-vec, CPU-only)
  and get injected into context when relevant. "Forget that" works too, and
  Baby quietly extracts durable facts from conversation on its own.
- **Personality**: warm, witty, replies in your language (English / Hindi /
  Hinglish). Chat vs. act is detected per message — "kaisa hai Baby?" just
  chats (zero tools), "close Spotify" acts.
- **Next-step suggestions**: after finishing a real task, Baby proposes the
  single most useful next action.
- **The Brain — a living-graph UI** at `http://127.0.0.1:8765` (`run.py --ui`):
  Baby's mind rendered as a graph (subsystems, brains and tools as nodes), with
  honest activity pulsing along the *real* turn path, a central status gauge, a
  click-a-node inspector for every subsystem, and a "Search the brain…" omnibox
  over facts / conversations / activity / tasks. Canvas-2D only — zero VRAM, the
  GPU stays reserved for the LLM. See [The Brain (v3 UI)](#the-brain-v3-ui).
- **Real tools**: system stats (CPU/RAM/GPU), open/close/focus apps, instant
  file search (Everything), read/write files, gated PowerShell, web search,
  page fetch, remember/recall/forget.
- **Safety gate**: deterministic ALLOW/CONFIRM/DENY classifier — destructive
  commands are refused outright, mutating ones need your explicit Yes (modal
  in the UI, y/N in the CLI), unknown commands never run unasked. Every call
  is audited to `baby.db`.
- CLI REPL (`run.py --cli`) with the same tools and confirmations.
- Conversations persist (SQLite, WAL) and resume across restarts; long
  sessions get rolling summaries so context never silently truncates.

## Setup

Requirements: Windows 11, Python 3.11+, an NVIDIA GPU with 8 GB VRAM.

```powershell
git clone <repo> baby && cd baby
powershell -ExecutionPolicy Bypass -File scripts\setup.ps1
```

The setup script installs/verifies Ollama, pulls the daily model
(`qwen3.5:9b-q4_K_M`, ~6.6 GB), sets Ollama tuning env vars
(`OLLAMA_FLASH_ATTENTION=1`, `OLLAMA_KV_CACHE_TYPE=q8_0`,
`OLLAMA_CONTEXT_LENGTH=8192`), installs `uv`, syncs `.venv`, and
pre-downloads the e5 embedding model (~470 MB), the Kokoro TTS model
(~340 MB), the wake-word models, and the Whisper cache — all of which run
on CPU; the GPU stays reserved for the LLM.

## Usage

```powershell
uv run python run.py --all    # everything: UI + voice + workers + telegram
uv run python run.py --voice  # web UI + voice (wake word, PTT, TTS)
uv run python run.py --ui     # web UI at http://127.0.0.1:8765
uv run python run.py --cli    # terminal REPL
```

```
you> what time is it?
baby>
  [tool: get_time({})]
  → "Friday, 03 July 2026, 18:42:10"
It's 6:42 PM on Friday, July 3rd.
```

Telegram setup (optional): create a bot with **@BotFather**, get your chat
id from **@userinfobot**, put both in `.env`
(`TELEGRAM_BOT_TOKEN` / `TELEGRAM_CHAT_ID`), set `telegram.enabled: true`.
Cloud brains: `OPENROUTER_API_KEY` (primary), `NVIDIA_API_KEY` (NIM heavy)
and `GEMINI_API_KEY` (backstop) in `.env`.

## The Brain (v3 UI)

The default UI (`ui.frontend: v3`) is a React app that renders Baby's mind as a
**living graph** — an honest, event-driven visualization over the router, voice,
memory and tool paths that already exist. Nothing is faked: every pulse is driven
by a real bus event or pipeline state; idle nodes breathe rather than invent
motion.

- **Living graph** — subsystems, brains and tools are nodes on a pinned layout
  (voice west, brains center, tools east, memory south). Edge pulses trace the
  real turn path (wake → STT → router → brain → gate → tool → TTS), colored by
  safety class; the central **core node is a status gauge** (idle / listening /
  thinking / speaking / executing) driven by the synthesized `/ws/state`.
- **Node inspectors** — click any node for a blurb, live `/api/nodes/{id}/stats`,
  and per-type controls: enable/disable a tool (the model stops seeing its schema
  next turn — the safety gate has **no** disable control), cancel a queued task,
  run a schedule now, one-turn "prefer the strongest brain" boost, browse/wipe
  memory. The safety gate can never be bypassed from the UI.
- **"Search the brain…"** — a top-center omnibox with grouped results (Facts ·
  Conversations · Activity · Tasks); pick one and the camera flies to the anchor
  node and opens its inspector. Full keyboard nav (Ctrl/⌘-K or `/`, ↑↓, Enter,
  Esc).
- **Honest connection + perf** — a reconnect pill when a socket drops (a
  mid-stream reply is finalized cleanly, never a frozen cursor); a `performance
  mode` toggle and `prefers-reduced-motion` throttle the animation; the graph is
  canvas-2D and stays inside a strict GPU/CPU budget.
- **Responsive** — usable on a phone over Tailscale: the graph stays full-bleed
  and pannable while the chat panel and inspector become slide-over drawers.

**Rollback is one line.** `ui.frontend: classic` serves the original vanilla UI,
and `/classic` is always available regardless of the flag — the daily-driver
parity target lives on. The React app is built on setup (`ui/app/dist`, not
committed).

### Native app + 3D brain (v4.0.0)

v4 (branch `feature/v4-native-3d-brain`) wraps the **same** FastAPI-served UI in a
thin **Tauri** desktop shell (native window, tray, single-instance, close-to-tray,
attach-or-spawn) and upgrades the graph to a **3D neural sphere**. The shell bundles
no second copy of the UI — it loads `127.0.0.1:8765`, so the browser and the native
window render the identical build (DECISIONS #119). Two code-defaulted rollback
flags gate it, both non-bricking:

- `ui.shell: browser | native` — `browser` (default) just means "don't launch the
  exe"; the `127.0.0.1:8765` UI is untouched. Native shell shipped in V1.
- `ui.brain: 2d | 3d` — code-default **`3d`**, the WebGL neural sphere (the V2
  governor auto-demotes to the 2D floor under VRAM/frame pressure, so 3d-by-default is
  self-protecting); `ui.brain: 2d` is the one-line rollback to the v3 canvas graph.
  Shipped in V3.

**Docker-Desktop model (V1).** The assistant runs as an always-on background
service; the native window **attaches** to it — or **spawns** one (`pythonw run.py
--all`) if none is running, polling `127.0.0.1:8765` until ready, then loading the
UI. Closing the window (X) **hides to tray**; the tray shows Baby's status
(green ready / amber working / red waiting on a confirmation, folded off
`/ws/activity`) and its only exit is **"Quit Baby (app)"**, which closes the window
and stops *only* a backend the shell itself spawned — an attached always-on service
keeps running (Telegram, scheduler, background tasks). Stopping the **service** is a
separate, documented action *outside* the app — `scripts\autostart.ps1 -Remove` —
never an app menu item and never an HTTP endpoint (DECISIONS #120, #122).

Build/run the native shell:

```powershell
scripts\setup.ps1                      # installs Rust + builds the shell (fail-soft)
npm --prefix ui/shell run build        # -> NSIS installer + baby-shell.exe
scripts\dev_app.ps1                    # dev: window against the ui/app Vite server
scripts\autostart.ps1 -Shell native    # open the window (attaching) at logon too
```

### Screenshots

<!-- Owner: capture during the soak and drop into docs/img/ -->
- `docs/img/brain-graph.png` — the living graph mid-turn (pulses + core gauge)
- `docs/img/brain-inspector.png` — a node inspector drawer
- `docs/img/brain-search.png` — the "Search the brain…" omnibox
- `docs/img/brain-turn.gif` — the graph pulsing during a voice turn

## Config

`config.yaml` is the reference (richly commented). The v3 + v4 knobs:

```yaml
ui:
  frontend: v3          # v3 = "The Brain" React app; classic = vanilla rollback
                        # /classic is always served regardless of this flag
  # --- v4: both code-defaulted, non-bricking rollback flags ---
  shell: browser        # browser (default) = open the UI in a browser; native =
                        # launch the Tauri desktop shell (wired in V1). Rollback =
                        # shell: browser; the 127.0.0.1:8765 UI is untouched.
  brain: 3d             # 3d (code-default) = the WebGL neural sphere; 2d = the v3
                        # canvas graph (rollback). Non-bricking either way — the
                        # governor auto-demotes to the 2d floor under GPU pressure.

render:                 # v4 frame governor (V2), all code-defaulted
  target_fps: 60        # the frame budget the governor protects
  tier: auto            # auto = full3d ceiling; lite3d / 2d cap the quality lower
  idle_full_on_desktop: true

voice:
  speaker_verify:
    enabled: false      # v2 ships OFF; flip on only if the FAR/FRR report clears
    model: models/wespeaker_en_voxceleb_CAM++.onnx   # bench winner from the report
    mode: chat_only     # chat_only (enforce) | ignore (drop) | observe (score+log)
    accept_threshold: 0.62   # tuned from the soak report
    reject_threshold: 0.45
    smoothing_window: 5      # utterances averaged for the session trust score
    demote_after: 2          # sustained low scores before demoting to chat-only
```

Cloud keys go in `.env` (`OPENROUTER_API_KEY`, `NVIDIA_API_KEY`,
`GEMINI_API_KEY`); the UI binds to `127.0.0.1` only.

## Brain routing (cloud-primary)

```
turn arrives
├─ privacy pin (read_file / run_shell result in context) → local 9B only
├─ language pin (≥30% Devanagari)                        → local 9B only
├─ game mode ON (local unloaded)                         → cloud → Gemini, no local
├─ health OFFLINE                                        → local 9B only
├─ health DEGRADED                                       → Gemini → local (probes recover cloud)
├─ rate bucket empty (36 RPM)                            → local 9B, cloud skipped entirely
├─ heavy turn (planning / orchestrator)                  → NIM heavy → cloud primary → local
└─ normal turn                                           → cloud primary → Gemini → local

cloud primary = openai/gpt-4o-mini via OpenRouter (bench: DECISIONS #83);
heavy = z-ai/glm-5.2 via NVIDIA NIM (background-only)
```

- Health state machine: one failure drops CLOUD→DEGRADED (DNS straight to
  OFFLINE); recovery needs 3 consecutive 45 s probes plus a budgeted
  1-token generation ping. A 429 starts a 90 s cloud cooldown.
- Per-request fallback is mid-agent-loop: the identical messages array is
  resent to the next rung — no restart, no lost tool results. First-token
  timeout: 3.5 s on voice, 8 s on text.
- Privacy pins never leak: pinned tool results force the rest of the turn
  local (outranking game mode) and are redacted from any cloud-bound
  payload. The UI shows which brain answered every message (badge), the
  router state (dot) and a game-mode toggle.
- **Rollback**: `router.mode: local_primary` in config.yaml — one line,
  restores the pre-NIM local-first ladder.

## Development

```powershell
uv run ruff check .
uv run pytest
```

Unit tests never touch the network — the agent loop is tested against a scripted
`FakeProvider`.

## Roadmap

| Phase | Delivers |
|-------|----------|
| 0 ✅ | Skeleton: provider, agent loop, registry, CLI, persistence |
| 1 ✅ | Real tools (files/shell/web/apps), safety gate, web UI + activity feed |
| 2 ✅ | Long-term memory (sqlite-vec), persona, chat-vs-act detection |
| 3 ✅ | Voice: wake word, Whisper STT, Kokoro TTS, EN/HI/Hinglish |
| 4 ✅ | Background tasks, notifications, browser control, Telegram, autostart |
| 5 ✅ | Multi-agent projects, screen awareness, speaker verification, Tailscale doc |
| v1.1.0 ✅ | Cloud-primary brains (OpenRouter primary + NIM heavy + Gemini backstop), health-aware router, privacy/language pins, game mode, live E2E battery, soak-report tooling |
| v1.1.1 ✅ | Hotfix: sensor tool contract (#6) + DB poison hygiene (#7) |
| v2.0.0 ✅ | Conversation mode + proceed/cancel (#2, #4), memory v2 — budgeted context + cross-session RAG + clear/wipe (#3, #5), token telemetry (#8) |
| v3.0.0 ✅ | **The Brain** — living-graph UI (honest pulses, status gauge, node inspectors, "Search the brain…" omnibox) over a read-only graph data spine; speaker verification v2 (multi-centroid, session-trust, ships OFF); responsive + reconnect-resilient |
| v4.0.0 ✅ | **Native app + 3D neural brain** — a thin Tauri desktop shell over the same FastAPI-served UI (attach-or-spawn, close-to-tray, single-instance, native tray); the living graph reborn as a 3D neural sphere (honest firing, mic/TTS amplitude gauge, router recolor, game-mode ghost); a 60 fps frame governor + VRAM watchdog; a CSS-first motion system; both rollback flags non-bricking (`ui.shell: browser`, `ui.brain: 2d`) |
