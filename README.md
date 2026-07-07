# Baby

A Jarvis-style, voice-enabled personal AI assistant for Windows 11.
**Cloud-primary brains, local-first guarantees**: fast cloud models serve by
default, but privacy-pinned turns never leave the PC, and the warm local 9B
keeps everything working with the Wi-Fi cable pulled.

> Status: **v1.1.0 — cloud-primary brain migration shipped** ✅
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
- **Speaker verification** (off by default): enroll once
  (`scripts\enroll_voice.py`, ~2 min) and only YOUR voice can trigger
  actions — anyone else gets polite chat with every tool denied at the
  safety gate. Disabled in the stock config after real-world testing
  (CAM++ false-rejected natural speech); the code and enrollment stay for
  a future retry. "baby stop" works for any voice; PTT bypasses the check.
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
- **Web UI** at `http://127.0.0.1:8765` (`run.py --ui`): streaming chat +
  a live activity feed showing every tool call, its safety class, and result.
  `GET /memory` lists stored facts.
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

## Brain architecture (cloud-primary)

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
