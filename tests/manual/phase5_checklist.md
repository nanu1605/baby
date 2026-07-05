# Phase 5 Manual Acceptance Checklist

Run `uv run python run.py --all`. All of these must pass before Phase 5 is
signed off (spec §16, Phase 5). Prereqs: GEMINI_API_KEY in .env,
`scripts\enroll_voice.py` run once, setup.ps1 re-run (speaker model).

## 1. Multi-agent orchestrator (feature #9)

- [ ] "Baby, start a project: build me a starter FastAPI project with auth
      and tests" → reply contains a project_id immediately; feed shows
      `project #N started: … (K subtasks)`.
- [ ] Activity feed shows per-worker progress (`task #N started/finished`
      lines for each subtask) while chat stays usable.
- [ ] Planning ran on the best brain: badge/feed show heavy (RAM ≥ 22 GB
      free) or the denial line ("heavy denied: X GB free < 22") followed by
      cloud; with no key AND low RAM it says it stayed on daily.
- [ ] On completion: integrated result announced (toast + spoken) and
      `GET /projects` (or "project status?") shows the plan + subtask
      outcomes.
- [ ] A project whose spec mentions a gated action ("delete old logs across
      my projects") → confirm modal BEFORE queuing.
- [ ] "Cancel project N" mid-run → project cancelled, unfinished subtasks
      cancelled.

## 2. Screen awareness (+screen)

- [ ] "What's on my screen?" (typed) → accurate description of the visible
      app/content. `nvidia-smi` during the call: no second model loaded
      (the resident 9B answers).
- [ ] Same by voice → spoken description.
- [ ] A pointed question ("what error is shown on my screen?") → answers it.
- [ ] Break the local path (set `screen.model: no-such-model`, restart) →
      feed shows "screen: local vision failed — sending the screenshot to
      Gemini" and the answer still arrives. Restore config after.
- [ ] Set `screen.allow_cloud_fallback: false` + broken model → clean error
      reply, no screenshot leaves the machine. Restore config.

## 3. Speaker verification (+speaker-id)

- [ ] Boot log line `voice: speaker verify ready … (on (threshold X))` after
      enrollment; `/stats` shows `speaker_verify: on…`.
- [ ] Your voice: "hey jarvis … close Spotify" → works as before.
- [ ] Another person (or a recording of another voice): "hey jarvis …
      delete test.txt on my desktop" → Baby answers chat-style but the
      action is refused; feed shows the DENY ("voice not recognized as the
      owner — chat only"). Nothing was deleted.
- [ ] Same person, plain question ("what time is it?") → gets a spoken
      answer (chat still works) but the get_time tool call is denied —
      Baby answers from context or says it can't check.
- [ ] "baby stop" from the OTHER person mid-reply → playback stops (kill
      phrases work for any voice).
- [ ] Push-to-talk (ctrl+alt+b) → actions work without verification
      (keyboard = owner).
- [ ] Set `voice.speaker_verify.mode: ignore`, restart: unknown voice gets
      SILENCE + feed line "unknown speaker ignored". Restore `chat_only`.
- [ ] Delete/rename `models/owner_voice.json`, restart → boot says
      verification off, voice works exactly as Phase 4. Restore it.

## 4. Tailscale (docs)

- [ ] Follow docs/TAILSCALE.md: UI loads on the phone over
      `https://….ts.net` with Wi-Fi off (mobile data).
- [ ] A device outside the tailnet cannot connect.
- [ ] `tailscale serve reset` restores localhost-only.

## 5. Regression

- [ ] Voice loop, browser, background tasks, briefing, telegram (if
      enabled), tray, autostart still work.
- [ ] VRAM on daily turns unchanged (~8 GB); a screen query does not evict
      the 9B (default config).
- [ ] `uv run pytest` green; `uv run ruff check .` clean.

Sign-off: Tanishq — Phase 5 confirmed. (Spec marks Phase 5 open-ended; the
portfolio checkpoint — polished README + demo video — is the recommended
next milestone.)
