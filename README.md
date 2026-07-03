# Baby

A Jarvis-style, voice-enabled, **local-first** personal AI assistant for Windows 11.
Local models by default (privacy, zero cost); free cloud tier only as a fallback brain.

> Status: **Phase 1 — Text Agent, Real Tools & UI** ✅
> Full build plan: [BABY_PROJECT_PLAN.md](BABY_PROJECT_PLAN.md)

## What works right now

- **Web UI** at `http://127.0.0.1:8765` (`run.py --ui`): streaming chat +
  a live activity feed showing every tool call, its safety class, and result.
- **Real tools**: system stats (CPU/RAM/GPU), open/close/focus apps, instant
  file search (Everything), read/write files, gated PowerShell, web search,
  page fetch.
- **Safety gate**: deterministic ALLOW/CONFIRM/DENY classifier — destructive
  commands are refused outright, mutating ones need your explicit Yes (modal
  in the UI, y/N in the CLI), unknown commands never run unasked. Every call
  is audited to `baby.db`.
- CLI REPL (`run.py --cli`) with the same tools and confirmations.
- Conversations persist (SQLite, WAL) and resume across restarts.

## Setup

Requirements: Windows 11, Python 3.11+, an NVIDIA GPU with 8 GB VRAM.

```powershell
git clone <repo> baby && cd baby
powershell -ExecutionPolicy Bypass -File scripts\setup.ps1
```

The setup script installs/verifies Ollama, pulls the daily model
(`qwen3.5:9b-q4_K_M`, ~6.6 GB), sets Ollama tuning env vars
(`OLLAMA_FLASH_ATTENTION=1`, `OLLAMA_KV_CACHE_TYPE=q8_0`,
`OLLAMA_CONTEXT_LENGTH=8192`), installs `uv`, and syncs `.venv`.

## Usage

```powershell
uv run python run.py --ui     # web UI at http://127.0.0.1:8765 (recommended)
uv run python run.py --cli    # terminal REPL
```

```
you> what time is it?
baby>
  [tool: get_time({})]
  → "Friday, 03 July 2026, 18:42:10"
It's 6:42 PM on Friday, July 3rd.
```

`--voice`, `--all` arrive in later phases.

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
| 2 | Long-term memory (sqlite-vec), persona, chat-vs-act detection |
| 3 | Voice: wake word, Whisper STT, Kokoro TTS, EN/HI/Hinglish |
| 4 | Background tasks, notifications, browser control, Telegram, autostart |
| 5 | Multi-agent orchestration, screen awareness, speaker verification |
