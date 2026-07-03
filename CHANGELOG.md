# Changelog

## Phase 1 — Text Agent, Real Tools & UI (2026-07-03)

- Safety gate (`core/safety.py`): deterministic DENY-first classifier with
  chain/scriptblock/subexpression extraction, encoded-command and IEX
  pre-checks; unknown commands default to CONFIRM. 60+ test cases.
- Confirmation flow: 60 s timeout → auto-NO; answerable from UI modal or CLI
  y/N prompt; kill switch cancels all pending.
- Event bus (`core/bus.py`): one emission path for every surface; audit rows
  written inline (durable), bus mirrors live.
- Tools: `get_system_stats` (psutil + NVML), `app_control`
  (Start-Menu index, WM_CLOSE→kill), `file_search` (Everything SDK IPC +
  scandir fallback), `read_file` (markitdown for pdf/docx), `write_file`
  (home-only), `run_shell` (gated PowerShell, UTF-8, 8 KB cap),
  `web_search` (ddgs), `fetch_page` (trafilatura).
- Audit log: every tool call → `audit_log` with class/approval/result.
- Web UI at 127.0.0.1:8765: streaming chat pane, live activity feed with
  safety-class colors, confirmation modal with countdown, header gauges
  (CPU/RAM/VRAM), Stop button. Vanilla JS, no build step.
- CLI rewired onto the bus; readiness sequence shared (`core/readiness.py`)
  with a "Baby ready" toast (winotify).
- setup.ps1: Everything install (winget) + SDK DLL download + autorun key.

## Phase 0 — Skeleton & Heartbeat (2026-07-03)

- Repo scaffold: uv-managed `pyproject.toml`, `config.yaml`, `.env.example`.
- SQLite store (`baby.db`, WAL) with full schema; conversation + message persistence.
- `ChatProvider` protocol + Ollama provider (OpenAI-compat, streaming, tool calls).
- Minimal `AgentCore` loop: message → model → tool → observe → reply, 8-iteration cap.
- Tool registry with `@tool` decorator (schema from type hints) + `get_time` dummy tool.
- CLI REPL (`python run.py --cli`) with streaming output and conversation resume.
- `scripts/setup.ps1`: Ollama install/check, model pull, env tuning, uv sync.
- Tests: agent loop with FakeProvider (tool threading, iteration cap, error recovery).
