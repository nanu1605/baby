# Baby

A Jarvis-style, voice-enabled, **local-first** personal AI assistant for Windows 11.
Local models by default (privacy, zero cost); free cloud tier only as a fallback brain.

> Status: **Phase 4 — Autonomy, Notifications, Reach** ✅
> Full build plan: [BABY_PROJECT_PLAN.md](BABY_PROJECT_PLAN.md)

## What works right now

- **Background tasks**: "in the background, research the top 3 EVs under
  15 lakh" → Baby hands back a task id and keeps chatting; when the task
  finishes you get a toast, a spoken announcement, and (if enabled) a
  Telegram message. `task_status` / `cancel_task` / `GET /tasks` to manage.
- **Three brains**: daily 9B stays warm; "use the big brain" (or a planning
  task, a failed retry, a huge input) escalates to the 35B — gated on free
  RAM — or to Gemini's free tier. The activity feed narrates every routing
  decision; the header badge shows which brain answered.
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
Cloud escalation (optional): put a free `GEMINI_API_KEY` in `.env`.

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
| 5 | Multi-agent orchestration, screen awareness, speaker verification |
