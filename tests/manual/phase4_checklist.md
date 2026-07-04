# Phase 4 Manual Acceptance Checklist

Run `uv run python run.py --all` (or `--ui` for text-only checks).
All of these must pass before Phase 5 starts (spec §16, Phase 4).

## 1. Background tasks (feature #10)

- [ ] "In the background, research the top 3 EVs under 15 lakh and summarize"
      → reply contains a task_id immediately; chat stays usable while it runs.
- [ ] Activity feed shows `task #N queued/started/finished` lines.
- [ ] On completion: Windows toast + spoken announcement ("Baby: your task
      '…' is done") + Telegram push (if enabled). One-line result included.
- [ ] "What's the status of my tasks?" → task_status tool lists them.
- [ ] "Cancel task N" while running → status `cancelled`, no notification.
- [ ] A task whose spec mentions a gated action ("delete old downloads in the
      background") → confirm modal BEFORE queuing.

## 2. Model router escalation

- [ ] "Use the big brain: design a backup strategy for my projects" →
      activity feed shows "thinking harder — using the big brain" (or the
      denial "heavy denied: X GB free < 22" followed by the cloud tier when
      RAM is short). Header badge changes while the turn runs.
- [ ] With Ollama's heavy model present and >22 GB free RAM: heavy actually
      answers (first token can take a minute — the feed says so; nvidia-smi
      shows the 9B evicted during, restored on the next daily turn).
- [ ] With GEMINI_API_KEY set: paste a very long text (>4K tokens) → routes
      to cloud (never heavy — same 8K ctx). Without the key → stays daily
      and says so.
- [ ] Every routing decision/denial lands in the audit log (channel=router).

## 3. Browser (browser_act)

- [ ] "Open ollama.com in the browser and read me the main heading" →
      visible Chromium window opens, text comes back in the reply.
- [ ] First "click X on this site" → confirm modal naming the domain;
      approve → later clicks on the SAME domain run without confirm;
      a different domain asks again.
- [ ] Log into a site manually in Baby's browser window; restart Baby;
      the login persisted (profile at %LOCALAPPDATA%\baby\browser).
- [ ] "Take a screenshot of this page" → PNG path under
      %LOCALAPPDATA%\baby\shots.

## 4. Morning briefing

- [ ] Set `briefing.cron` to 2 minutes from now, restart → briefing is
      SPOKEN (voice on) + toast, covering date/weather/tasks/headlines/health.
- [ ] Restore `cron: "0 8 * * *"`. Next morning it fires at 08:00, or within
      an hour of waking the PC (misfire grace).

## 5. Telegram (feature +phone)

- [ ] BotFather token + chat id in .env, `telegram.enabled: true`, restart →
      boot log shows "telegram ready (owner chat only)".
- [ ] Message Baby from YOUR phone → reply arrives in Telegram.
- [ ] Message the bot from another account → silence (feed logs "ignored
      message from foreign chat").
- [ ] Ask for a gated action from the phone → inline ✅/❌ buttons; ✅ runs
      it, ❌ refuses; letting it sit 60 s auto-refuses.
- [ ] Background task completion pushes a Telegram message.

## 6. Autostart (feature #2 — LAST, after everything above is stable)

- [ ] `powershell -ExecutionPolicy Bypass -File scripts\autostart.ps1` →
      task "Baby Assistant" appears in Task Scheduler.
- [ ] Sign out and back in → NO window, "Baby ready" cue plays shortly after
      the desktop appears, UI reachable at http://127.0.0.1:8765.
- [ ] `%LOCALAPPDATA%\baby\logs\baby.log` collects the boot log.
- [ ] `scripts\autostart.ps1 -Remove` unregisters cleanly.

## 7. Regression

- [ ] Voice loop still works (wake → question → spoken reply).
- [ ] Spoken replies contain no "asterisk asterisk" (markdown stripped).
- [ ] Tray icon visible; turns amber during a turn, red while a confirm
      modal waits, back to green after; Open Baby / Quit Baby menu works.
- [ ] An announcement queued while you're mid-conversation waits until the
      conversation is idle, then plays.
- [ ] VRAM on daily turns unchanged (~8 GB, all Ollama).

Sign-off: Tanishq — Phase 4 confirmed → begin Phase 5 (multi-agent, screen
awareness, speaker verification).
