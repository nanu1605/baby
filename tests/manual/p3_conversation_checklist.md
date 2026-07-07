# P3 Manual Acceptance Checklist — Conversation Mode & Proceed/Cancel

Run `uv run python run.py --voice` (speaker + mic). Covers V2 items #2
(continuous conversation) and #4 (proceed/cancel). Tick every box.

Prereqs: `conversation.enabled: true` (config.yaml, default). The pretrained
**"hey jarvis"** carries the demo; the custom single-word **"jarvis"**
(`models/jarvis.onnx`) is optional and only needed for §5.

## 0. Automated gate (must be green)

- [ ] `uv run pytest -q` → all pass (600+).
- [ ] `uv run pytest tests/test_intents.py tests/test_safety.py -q` → green.
- [ ] `uv run ruff check .` → `All checks passed!`.

## 1. Conversation mode — wake-free follow-ups (#2)

- [ ] Say **"Hey Jarvis"** → beep → ask a question → Baby answers, then a soft
      cue opens the follow-up window; the feed shows `voice: listening (follow-up)`.
- [ ] Ask **4 more questions with NO wake word** between them (5-turn total
      back-and-forth). Each is answered; the window re-opens after every reply.
- [ ] Say **"Baby stop listening"** (or "bas") → soft cue, feed shows
      `voice: conversation ended`, and Baby goes idle (no new turn started).
- [ ] Re-open with "Hey Jarvis", then stay **silent ~60 s** → the window
      auto-closes with a cue (`conversation ended (silence)`), NOT after ~5 s.
- [ ] Baby never answers itself: it does not transcribe its own spoken reply as
      a new turn (listen only opens after playback fully ends).
- [ ] Barge-in still works: interrupt Baby mid-sentence by speaking → it stops
      and listens.
- [ ] Wake, then pause **~8 s** before asking → Baby is still listening and
      captures the question (does NOT close at ~5 s with `voice: heard nothing`).
      Tune `voice.listen_grace_s` if you want a longer/shorter window.
- [ ] Kill switch while speaking: during a long reply press **■ Stop** in the UI
      → Baby's voice cuts off within a beat AND the turn stops (feed:
      `voice: stopped by kill switch`); it returns to idle, no follow-up window.

## 2. Proceed / cancel (#4)

- [ ] Finish a real task so Baby ends with an **offer** ("…Want me to … ?").
      In the follow-up window say **"haan kar do"** / **"yes"** → the offered
      action runs. A CONFIRM-class action (e.g. delete/close) STILL shows its
      own confirmation modal — approving the offer does NOT skip the gate.
- [ ] Trigger another offer, answer **"nahi"** / **"no"** → Baby acknowledges
      ("Okay, I'll skip that." / Hindi) and does **not** run it.
- [ ] Trigger another offer, then ask an **unrelated question** → it's treated
      as a brand-new turn; the offer silently expires (not executed).
- [ ] Casual/backchannel safety: after an offer, laugh **"ha ha"** or say
      **"ok thanks"** → the offered action does **NOT** run (treated as a new
      turn, not a yes).

## 3. Text (UI/CLI) proceed/cancel

- [ ] In the web UI (`http://127.0.0.1:8765`) or CLI, do a task that ends with an
      offer, reply "yes" → runs; reply "no" → skipped. Same one-shot behavior.
- [ ] CLI CONFIRM prompt: a gated command → answer "haan"/"y" approves,
      "n"/anything else denies (shared `core/intents.parse_yes`).

## 4. Disable switch

- [ ] Set `conversation.enabled: false` in config.yaml, restart → after a reply
      Baby returns to IDLE (classic one-shot, wake word needed each turn).
      Restore `true` after.

## 5. Custom "jarvis" wake model (only if trained)

- [ ] With `models/jarvis.onnx` present, readiness shows
      `wake word ready (jarvis+hey_jarvis)`.
- [ ] Both **"Jarvis"** and **"Hey Jarvis"** wake Baby.
- [ ] False-accept sanity: normal nearby conversation + a short video that says
      "Jarvis" trigger rarely; tune `voice.wakeword_threshold` toward 0.6+ from
      the `voice: heard nothing` vs `voice: listening` ratio in the feed.

---

Done when 0–4 pass (5 only if the custom model is trained). Notes: an errored
turn (e.g. cloud down) also opens a follow-up so you can retry without a wake
word — expected.
