# Phase 1 — Manual Demo Checklist

Run with `uv run python run.py --ui`, open http://127.0.0.1:8765.

## Acceptance demos (spec §16 Phase 1)

- [ ] UI loads: dark two-pane page, model badge shows `qwen3.5:9b-q4_K_M`,
      CPU/RAM/VRAM gauges move. A "Baby ready" toast appeared at startup.
- [ ] Type a message → tokens stream into the assistant bubble live.
- [ ] **"Close Chrome and tell me my CPU and GPU usage"** (open Chrome first)
      → Chrome closes, stats in the reply, BOTH `app_control` and
      `get_system_stats` entries visible in the activity feed with green/amber
      borders and result summaries.
- [ ] **"Search my drive for invoices"** → results in seconds; feed entry shows
      `file_search`; result JSON contains `"engine": "everything"`.
- [ ] **"Search the web for today's USD to INR rate"** → current answer with
      source URLs; `web_search` (and maybe `fetch_page`) in the feed.
- [ ] **CONFIRM flow**: "create a folder called test-baby on my desktop" →
      amber modal shows the exact command + plain-English explanation +
      countdown. Click Yes → folder appears, feed entry flips to ✓.
- [ ] **DENY flow**: "run Remove-Item -Recurse -Force C:\\" → refused with
      reason, red feed entry, no modal, Baby explains.
- [ ] **Timeout**: trigger a CONFIRM, ignore it 60 s → auto-deny, modal closes.
- [ ] **Kill switch**: ask something slow, click ■ Stop mid-stream → turn ends
      with "(cancelled)".
- [ ] `baby.db` audit_log has one row per tool call incl. the denied one
      (`SELECT tool, safety_class, approved FROM audit_log ORDER BY id DESC`).

## Degradation

- [ ] Quit Everything (tray icon → Exit) → "search my drive for X" still works,
      result shows `"engine": "index"` (slower first time while index builds).
      Restart Everything afterwards.
- [ ] `python run.py --cli` still works: same tools, confirmations as y/N
      prompts in the terminal.

## Regression

- [ ] `uv run pytest` green (121+); `uv run ruff check .` clean.
