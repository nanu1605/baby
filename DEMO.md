# Baby — Demo Script

Target: a 2–3 minute portfolio video. The centerpiece is now **The Brain** — the
living-graph UI (`ui.frontend: v3`) pulsing over a real turn.

## The Brain (v3) — the money shot

1. `uv run python run.py --voice` on `ui.frontend: v3`; open
   `http://127.0.0.1:8765`. The graph settles — subsystems, brains and tools as
   nodes; the core node breathes at idle.
2. Speak a turn ("Hey jarvis… what's on my screen?"). Watch the pulse trace the
   **real** path — core → router → brain → gate → tool → back — the core gauge
   moving through thinking / executing / speaking, the answering brain recoloring.
3. Trigger a gated command ("delete this file") — an amber confirm pulse + the
   on-screen modal; approve/deny from the graph.
4. Click a node → the inspector: live stats, recent events, a tool enable/disable
   (send a turn and the model no longer offers it), task cancel, run-a-schedule.
5. Ctrl-K → "Search the brain…" a weeks-old topic → the camera flies to the
   anchor node and opens its inspector.
6. Toggle game mode → the local 9B node ghosts; pull Wi-Fi → brains recolor and
   the turn reroutes to local. Toggle `performance mode` → particles stop.
7. Kill the backend → a reconnect pill appears and any mid-stream reply finalizes
   cleanly; restart → the graph recovers on its own.

## Core assistant (still true)

- **Text/CLI**: `run.py --cli` — resumes the last conversation; `get_time` fires
  in the activity line against the real clock; history survives a restart.
- **Actions + safety**: "close Chrome and show me my CPU and GPU usage" lands in
  the feed; a destructive command is refused with a reason (the gate is never
  bypassed).
- **Memory**: "Remember my gym days are Mon/Wed/Fri" → later "When's my gym?"
- **Voice**: "Hey Baby, kaise ho?" — full EN/HI/Hinglish loop, barge-in, the
  spoken "Baby ready" cue on login.
- **Background + reach**: a background research task announces + toasts on
  completion; the morning briefing; Telegram from the phone; a multi-agent
  project build.
- **Speaker verification v2** (only when enabled after the soak): a stranger's
  "delete X" is politely refused (chat-only) while your identical command runs.

## Rollback

`ui.frontend: classic` (or open `/classic` directly) restores the original
vanilla panel — useful to show parity side by side.
