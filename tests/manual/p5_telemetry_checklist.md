# P5 Manual Acceptance Checklist — Token Telemetry (#8)

Covers V2 item #8 (per-turn token usage) plus the v2.0.0 soak + release gate.
Token capture is on by default (`telemetry.emit_usage: true`); set it `false`
for any single host that rejects `stream_options.include_usage`.

## 0. Automated gate (must be green)

- [ ] `uv run pytest -q` → all pass.
- [ ] `uv run pytest tests/test_provider_usage.py tests/test_usage_telemetry.py tests/test_safety.py -q` → green.
- [ ] `uv run ruff check .` → `All checks passed!`.

## 1. Per-turn counts in the UI

- [ ] Web UI (`http://127.0.0.1:8765`): ask a normal question → the answer bubble
      shows an `↑prompt ↓completion` chip beside the brain badge.
- [ ] Hover the chip → tooltip shows the total; a **local** (Ollama) turn is
      tagged "local — no quota".
- [ ] A tool-using turn (e.g. "what's my CPU temp?") shows ONE chip whose counts
      cover the whole turn (tool round + final answer), not per round.

## 2. Header session + today totals

- [ ] The header `tok` badge updates within ~5 s of a turn; hover → "session N ·
      today M tokens" with a per-brain breakdown line.
- [ ] Restart Baby → **session** resets to the new boot; **today** persists
      (same calendar day). A turn after restart bumps both again.

## 3. Every brain reports (soak validation)

- [ ] Force each brain and confirm a non-zero row lands (feed chip + DB):
      cloud primary (normal turn), heavy ("use the big brain"), local (game mode
      off + a privacy-pinned turn or offline), Gemini backstop (if configured).
- [ ] `sqlite3 baby.db "SELECT brain_tier, COUNT(*), SUM(total_tokens) FROM usage_log GROUP BY brain_tier"`
      → one group per brain actually used, counts > 0.
- [ ] If a provider shows blank counts every turn, it likely 4xx'd on
      `include_usage`: set `telemetry.emit_usage: false` (or per-deploy) and
      confirm turns still work — telemetry degrades, never breaks the turn.

## 4. Accuracy vs provider dashboard

- [ ] After a day of use, `SELECT SUM(total_tokens) FROM usage_log WHERE date(ts,'localtime')=date('now','localtime')`
      is within a reasonable margin of the OpenRouter / NIM / Gemini console
      usage for the same window. (Local/Ollama has no quota to compare.)

## 5. Robustness (no telemetry may ever break a turn)

- [ ] A dropped/slow connection right after the model finishes still returns the
      full answer (bounded trailer wait, covered by
      `test_done_delivered_when_trailer_{stalls,errors}`) — no "stream stalled".
- [ ] Kill switch mid-turn: the turn cancels cleanly; no bogus `usage_log` row
      for the cancelled turn.

## 6. Release gate (v2.0.0)

- [ ] 2–3 day soak, everything on: session lengths, ~0 new quarantine events,
      RAG hit quality, tokens/day, **zero unhandled exceptions**.
- [ ] Full regression: `uv run pytest -q` + `python scripts/e2e_regression.py --with-project --fresh-conversation`
      + v1.1.0 manual demos (offline fallback, game mode, privacy pins).
- [ ] `pyproject.toml` is `2.0.0`; CHANGELOG/README/DECISIONS carry P5.
- [ ] Mark PR #2 Ready for Review → **owner merges + tags `v2.0.0`** (never Claude).

---

Rollback: `telemetry.emit_usage: false` stops requesting usage (feed/header show
blank); the `usage_log` table is inert and harmless if unused.
