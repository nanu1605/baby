# Baby

A Jarvis-style, voice-enabled, **local-first** personal AI assistant for Windows 11.
Local models by default (privacy, zero cost); free cloud tier only as a fallback brain.

> Status: **Phase 0 — Skeleton & Heartbeat** ✅
> Full build plan: [BABY_PROJECT_PLAN.md](BABY_PROJECT_PLAN.md)

## What works right now

- Chat with a local Qwen3.5 9B model (Ollama) from a CLI REPL, streaming.
- Real tool calling: the model can call `get_time` and use the result.
- Every conversation persists to `baby.db` (SQLite, WAL) and resumes on restart.

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
uv run python run.py --cli
```

```
you> what time is it?
baby>
  [tool: get_time({})]
  → "Friday, 03 July 2026, 18:42:10"
It's 6:42 PM on Friday, July 3rd.
```

`--ui`, `--voice`, `--all` arrive in later phases.

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
| 1 | Real tools (files/shell/web/apps), safety gate, web UI + activity feed |
| 2 | Long-term memory (sqlite-vec), persona, chat-vs-act detection |
| 3 | Voice: wake word, Whisper STT, Kokoro TTS, EN/HI/Hinglish |
| 4 | Background tasks, notifications, browser control, Telegram, autostart |
| 5 | Multi-agent orchestration, screen awareness, speaker verification |
