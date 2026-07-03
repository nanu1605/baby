# Baby — Demo Script

Target: a 2–3 minute portfolio video (full script from plan Appendix B applies
once Phases 0–3 land). Grows per phase; only shipped features are listed.

## Phase 0 demo (current)

1. `uv run python run.py --cli` — Baby comes up, resumes the last conversation.
2. "Hi Baby, what time is it?" — watch the `get_time` tool fire in the activity
   line and the answer use the real clock.
3. `exit`, relaunch, ask "what did I ask you before?" — history survived the
   restart (SQLite, WAL).

## Later phases (from plan Appendix B)

- Phase 1: "Close Chrome and show me my CPU and GPU usage" — actions land in the
  live activity feed; a destructive command is refused with a reason.
- Phase 2: "Remember my gym days are Mon/Wed/Fri" → later "When's my gym?"
- Phase 3: "Hey Baby, kaise ho?" — full voice loop, EN/HI/Hinglish, barge-in,
  unprompted "Baby ready" cue on login.
- Phase 4: background research task announces + toasts on completion; morning
  briefing; Telegram from the phone.
- Phase 5: "What's on my screen?"; multi-agent project build.
