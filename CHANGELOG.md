# Changelog

## Phase 0 — Skeleton & Heartbeat (2026-07-03)

- Repo scaffold: uv-managed `pyproject.toml`, `config.yaml`, `.env.example`.
- SQLite store (`baby.db`, WAL) with full schema; conversation + message persistence.
- `ChatProvider` protocol + Ollama provider (OpenAI-compat, streaming, tool calls).
- Minimal `AgentCore` loop: message → model → tool → observe → reply, 8-iteration cap.
- Tool registry with `@tool` decorator (schema from type hints) + `get_time` dummy tool.
- CLI REPL (`python run.py --cli`) with streaming output and conversation resume.
- `scripts/setup.ps1`: Ollama install/check, model pull, env tuning, uv sync.
- Tests: agent loop with FakeProvider (tool threading, iteration cap, error recovery).
