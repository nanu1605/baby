# Phase 0 — Manual Demo Checklist

Run each step; check the box only when observed exactly as described.

- [ ] `uv run python run.py --cli` starts, prints `Baby ready (text only) — model: qwen3.5:9b-q4_K_M`.
- [ ] Type a greeting → streamed reply appears token by token.
- [ ] Ask "what time is it?" → activity line shows `tool: get_time({})` and the
      reply contains the actual current time (compare with the system clock).
- [ ] Type `exit` → clean exit, no traceback.
- [ ] Press Ctrl+C mid-reply → clean `bye.`, no traceback.
- [ ] Restart `run.py --cli` → banner shows `[resuming conversation #N]`; ask
      "what did I ask you earlier?" → answer references the previous exchange.
- [ ] `baby.db` exists; `conversations` and `messages` tables contain the session.
- [ ] With Ollama stopped, `run.py --cli` prints "Baby could not start" and exits
      nonzero (no ready banner).
- [ ] `uv run ruff check .` and `uv run ruff format --check .` both pass.
- [ ] `uv run pytest` all green.
