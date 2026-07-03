# Phase 2 Manual Acceptance Checklist

Run `uv run python run.py --ui`, open http://127.0.0.1:8765.
All of these must pass before Phase 3 starts (spec Section 16, Phase 2).

## 1. Fact survives sessions (feature #5)

- [ ] Type: **"Remember my gym days are Mon/Wed/Fri."**
      → activity feed shows `remember` (green/allow), reply confirms.
- [ ] Visit http://127.0.0.1:8765/memory → the fact is listed.
- [ ] Close Baby completely (Ctrl+C the process). Start it again.
- [ ] Ask: **"When's my gym?"** → correct answer, ideally with **no** tool
      call (the fact arrives via injected memory).

## 2. Chat vs act, auto-detected (feature #11)

- [ ] Type: **"kaisa hai Baby?"** → friendly Hinglish reply, activity feed
      stays **empty** (zero tools fired), no "Next:" line.
- [ ] Type: **"close Spotify"** (have Spotify open) → `app_control` fires.
- [ ] Detection is automatic — Baby never announces which mode it picked.

## 3. Next-step suggestion (feature #8)

- [ ] Give a real task, e.g. **"Search my drive for BABY_PROJECT_PLAN.md."**
      → after the answer, the reply ends with a one-line **"Next: …"**
      suggestion.
- [ ] A pure chat message produces **no** suggestion.

## 4. Hinglish memory round-trip

- [ ] Type: **"yaad rakhna, mera favourite cricketer Virat Kohli hai"**
      → `remember` fires.
- [ ] Ask: **"mera favourite cricketer kaun hai?"** → correct Hinglish answer.

## 5. Forget

- [ ] Type: **"forget my gym days"** → `forget` fires, reply confirms.
- [ ] /memory shows the fact with `active: 0`; asking about gym days no
      longer uses it.

## 6. Hygiene

- [ ] `uv run pytest` → 142 passing (includes the 21 memory tests).
- [ ] Boot log shows **"memory ready (N facts)"**.
- [ ] After a long conversation (10+ messages), `conversations.summary` in
      `baby.db` is non-empty (rolling summary fired in the background).

Sign-off: Tanishq — Phase 2 confirmed → begin Phase 3 (Voice).
