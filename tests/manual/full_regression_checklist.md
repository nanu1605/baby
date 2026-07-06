# Full-Course Regression Checklist (N5 gate)

Human-only items — everything a script can't do (mic, reboot, Wi-Fi, a real
game). The automated half runs first:

```powershell
uv run pytest -q                                        # offline suite
uv run python scripts/e2e_regression.py --with-project  # live battery (warn: browser opens, Baby may speak)
```

Both must be green before starting this list. Run sections in order —
autostart LAST (spec §16). Carries the original Phase 1–4 demo lines by name
(NIM plan N5 requires re-verifying them verbatim).

## 1. Voice loop (Phase 3)

- [ ] Wake word → "what time is it?" → spoken reply, correct time.
- [ ] Ctrl+Alt+B push-to-talk works without the wake word.
- [ ] Kill phrase ("baby stop") halts a long spoken reply mid-sentence.
- [ ] Barge-in: speak over Baby while it talks → it stops and listens.
- [ ] 3-language loop: one question in English, one in हिन्दी, one in
      Hinglish → each answered in its own language, Hindi spoken with the
      Hindi voice.
- [ ] No "asterisk asterisk" or markdown read aloud.

## 2. Routing physical (NIM N2)

- [ ] Mid-conversation, Wi-Fi OFF → next turn answers locally (badge
      "local"), feed shows `cloud state … → offline`, no crash, no dead air.
- [ ] Wi-Fi ON → within ~3 minutes feed shows `degraded → cloud (recovered)`;
      next turn badge shows "NIM" again.
- [ ] During DEGRADED, turns respond immediately (no repeated 3.5 s stalls).

## 3. Game mode real (NIM N3)

- [ ] Voice "game mode on" → VRAM drops ~6.5 GB (nvidia-smi), header shows
      🎮, Baby still answers via cloud.
- [ ] Voice "game mode off" → toggle instant, ready cue when the brain is
      warm again.
- [ ] Auto-detect: launch a real game fullscreen → game mode ON by itself
      within ~10 s; alt-tab out → back OFF; a manual "game mode on" is NOT
      reversed by alt-tabbing.
- [ ] (If reachable) cloud dead during game mode → Baby SAYS the honest line
      and "game mode off" still works by voice/text.

## 4. Background work + notifications (Phase 4 demos, verbatim)

- [ ] "In the background, research the top 3 EVs under 15 lakh and summarize"
      → task id immediately, chat stays usable, toast + spoken announcement
      on completion (+ Telegram if enabled).
- [ ] A task spec mentioning a gated action ("delete old downloads in the
      background") → confirm modal BEFORE queuing.
- [ ] Briefing: set `briefing.cron` 2 minutes ahead, restart → spoken +
      toast briefing; restore `"0 8 * * *"` after.
- [ ] "Use the big brain: design a backup strategy for my projects" →
      badge/feed shows the heavy brain (or its documented fallback).

## 5. Browser trust (Phase 4)

- [ ] "Close Chrome" (the ORIGINAL Phase 1 demo) → confirm modal → approved
      → Chrome closes, spoken confirmation.
- [ ] First click/type on a site → confirm names the domain; same-domain
      actions skip the modal afterwards; new domain asks again.
- [ ] Log into a site in Baby's browser; restart Baby; login persisted.

## 6. UI + pins

- [ ] Every assistant message shows a brain badge; hover shows model +
      routing reason.
- [ ] "Read me <some file>" → whole turn on local (badge "local", feed says
      `privacy pin (read_file)`).
- [ ] A Devanagari message → `language pin` in the feed, local badge.
- [ ] State dot: green (cloud) normally; amber after a cloud failure; 🎮
      during game mode.
- [ ] Kill button cancels a running turn; tray icon amber during turns, red
      on confirm, menu Open/Quit works.

## 7. Memory + screen (Phases 2/5)

- [ ] "Remember my favourite chai spot is <X>" → new fact in the Memory
      panel; next day "where do I like chai?" recalls it.
- [ ] "What's on my screen?" → sane one-liner from the LOCAL brain (feed
      shows no Gemini fallback unless local vision genuinely failed).

## 8. Rollback verification (NIM spec — PR checklist line)

- [ ] Set `router.mode: local_primary` in config.yaml → restart → 3 turns
      answer normally (legacy ladder; badges gone from /stats router shape
      is fine) → set back to `cloud_primary` → restart → cloud answers again.
      No code change either direction.

## 9. Autostart (LAST — Phase 4)

- [ ] Reboot the PC → no window, "Baby ready" cue after the desktop
      appears, UI reachable, `%LOCALAPPDATA%\baby\logs\baby.log` collecting.

Sign-off: Tanishq — full course green → PR #1 Ready for Review → merge →
tag v1.1.0.
