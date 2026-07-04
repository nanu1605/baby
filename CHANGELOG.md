# Changelog

## Phase 4 — Autonomy, Notifications, Reach (2026-07-04)

- Background tasks (`workers/queue.py`): "in the background, research X" →
  task_id immediately, chat stays usable; an asyncio pool (size 2) runs each
  task through a fresh AgentCore (15 tool-step budget) on its own
  `task:{id}` channel; progress lands in `task_events` + the activity feed.
  Tools: `start_background_task` (destructive specs need a confirm),
  `task_status`, `cancel_task`. `GET /tasks` read view.
- Notifications (`workers/notify.py`, feature #10): on task finish/fail —
  Windows toast + spoken announcement + Telegram push, each tier
  best-effort. Voice announcements queue on the pipeline (`announce_q`) and
  play only when Baby is idle — a live conversation always wins.
- Model router (`core/router.py`): daily → heavy → cloud escalation live.
  Triggers: explicit ("use the big brain" / "cloud pe pooch"), planning
  keywords, retry-after-failure, long context (>4K tokens → cloud only —
  heavy shares the global 8K ctx). Heavy (qwen3.6:35b-a3b) gated on >22 GB
  free RAM; Gemini free tier (OpenAI-compat endpoint, no extra SDK) cools
  down 5 min on 429/5xx. Every decision and denial → audit log + activity
  feed; `/stats` exposes the active tier for the header badge.
- Browser (`tools/browser.py`): `browser_act`
  goto/read/click/type/screenshot via Playwright Chromium, persistent
  profile in %LOCALAPPDATA%\baby\browser, visible window. Safety:
  goto/read/screenshot allowed; click/type confirm ONCE per domain per
  session — and the domain is read from the real page, never from model
  arguments.
- Scheduler (`workers/scheduler.py`): APScheduler cron over `schedules`
  rows + the morning briefing (08:00 local, fires up to 1 h late after
  wake) — date, Indore weather, pending tasks, headlines, system health,
  spoken + toast, composed by the agent with its normal tools.
- Telegram (`clients/telegram_bot.py`): polling bot embedded in the same
  asyncio loop, answers ONLY `TELEGRAM_CHAT_ID` (everything else logged and
  ignored); gated actions arrive as inline ✅/❌ buttons resolving the same
  confirmation manager as the web modal; task completions push to the phone.
- Autostart (`scripts/autostart.ps1`, feature #2): hidden Task Scheduler
  logon job running pythonw; run.py self-logs to
  %LOCALAPPDATA%\baby\logs\baby.log; `-Remove` unregisters.
- Deps: apscheduler, playwright, python-telegram-bot, python-dotenv (.env
  now actually loaded at boot). setup.ps1 installs Chromium, pulls the
  heavy model, creates %LOCALAPPDATA%\baby dirs.
- Tests: +80 (db tasks, router matrix, worker pool, notifier, browser +
  safety matrix, scheduler, telegram handlers). Suite: 278 passing.

## Phase 3 fix — owner testing feedback (2026-07-04)

- Voice worked only on the first try: the pause between the wake beep and
  the user's first word was counted as end-of-utterance silence, so every
  later capture ended before the user spoke and was dropped as empty. VAD
  endpointing now requires speech to start first (`vad_speech_wait_ms`,
  default 5 s grace after the beep); pure-silence captures skip the STT
  call entirely and the feed shows "voice: heard nothing" instead of
  failing silently.

## Phase 3 — Voice (2026-07-04)

- Full voice loop on a dedicated thread (the spec-allowed exception to the
  single-asyncio rule): wake word → beep → VAD-segmented capture → Whisper
  STT → the same AgentCore/SafetyGate/audit path as text → Kokoro TTS reply,
  sentence by sentence.
- Wake word (`voice/wakeword.py`): openWakeWord, interim **"hey jarvis"**
  model with auto-switch to `models/hey_baby.onnx` when the owner-trained
  file lands (`scripts/wakeword_training.md` has the Colab steps).
- STT (`voice/stt.py`): faster-whisper `large-v3-turbo`, CPU int8, 8 threads
  (~5.5 s/utterance; `voice.stt.model: small` is the faster/worse-Hindi
  knob). Hallucination gates: <0.3 s utterances dropped, junk list, VAD
  filter.
- TTS (`voice/tts.py`): kokoro-onnx (Kokoro-82M v1.0), per-sentence voice
  routing — Devanagari → `hf_beta` (Hindi), else `af_heart` (English);
  streaming sentence splitter with abbreviation + `।` handling;
  `python -m voice.tts --prerender` bakes `assets/baby_ready.wav`.
- Barge-in: talk over Baby and playback stops within ~100 ms, your
  interruption is captured immediately. Kill phrases ("baby stop",
  "baby ruk ja") cancel the turn outright. Push-to-talk: **ctrl+alt+b**
  (ctypes RegisterHotKey, no admin).
- Safety via voice: gated actions are spoken as "check the screen" — the
  confirm modal stays UI-only, the gate is untouched.
- Ready cue: cached-WAV "Baby ready" the moment the stack is live; degraded
  chime + toast if voice fails to load (text keeps working); no cue at all
  if the model is down.
- `run.py --voice` / `--all`; voice runs its own conversation on the shared
  provider/DB/bus/gate/memory. VRAM: voice adds **0** (all CPU) — measured
  8.04 GB during boot, all Ollama.
- Deps: faster-whisper, openwakeword, silero-vad, kokoro-onnx, sounddevice,
  onnxruntime. setup.ps1 downloads kokoro + wake models, warms the whisper
  cache, prerenders the ready cue.
- Tests: `tests/test_voice.py` (44 cases — sentence splitting, voice
  routing, kill phrases, bridge ordering, cross-thread publish/cancel, state
  machine, barge-in, ready cue) with all audio/model stages faked via DI.
  Suite: 192 passing.

## Phase 2 fixes — owner testing feedback (2026-07-03)

- Forget no longer resurrectable: forgotten facts keep their vectors so
  dedup can block the extractor from re-inserting them; explicit
  re-remember reactivates instead. `forget` deactivates every match above
  the floor (paraphrased duplicates used to survive and keep answering).
- Reply language pinned per turn: deterministic English/Hindi/Hinglish
  detection of the latest message, enforced via a trailing system nudge —
  English questions no longer get Hinglish replies.
- Tone mirrors the message: professional and emoji-free for work/serious
  questions, playful only for casual chat.
- Duplicate "Next:" lines fixed (model was imitating suggestions from
  history; the extra call now skips when one is already present).

## Phase 2 — Memory & Personality (2026-07-03)

- Long-term facts (`memory/store.py`): sqlite-vec `fact_vectors` (384-dim
  cosine) inside `baby.db`; dedup by embedding similarity ≥ 0.90 before
  insert; similarity floor + over-fetch/join-filter on search; `forget`
  deactivates the fact and removes its vector. Brute-force BLOB fallback if
  the extension can't load.
- Embeddings (`memory/embedder.py`): `intfloat/multilingual-e5-small` on CPU
  (sentence-transformers), with the e5 `query:`/`passage:` prefixes enforced
  in one place and tested.
- Rolling summary (`memory/summarizer.py`): every ~10 messages the daily
  model folds older turns into `conversations.summary` (≤ 200 tokens); the
  agent then loads history only past the watermark — no context double-spend.
- Fact extraction (`memory/extractor.py`): every ~20 messages the model
  proposes durable user facts as JSON; deduped by the store, own watermark.
- Tools: `remember`, `recall`, `forget` (all ALLOW — they touch only Baby's
  memory rows).
- Retrieval injection: top-k facts above the floor + the rolling summary are
  injected into the system prompt each turn under "What Baby remembers" /
  "Conversation so far".
- Baby persona (Appendix A) with automatic per-message chat-vs-act modes:
  "kaisa hai Baby?" chats with zero tools; "close Spotify" acts.
- Next-step suggestion (feature #8): after a turn with at least one
  successful tool call, one extra no-tools model call proposes a single next
  step, streamed and appended as "Next: …".
- Provider: `reasoning_effort` passthrough — qwen3.5 thinking burned tight
  `max_tokens` caps in the reasoning channel and returned empty content;
  internal calls (summary/extraction/suggestion) now disable thinking.
- UI: `GET /memory` read view. Boot prints "memory ready (N facts)" and
  degrades to Phase-1 behavior if the memory stack can't load.
- DB: `conversations.summarized_upto`/`extracted_upto` with in-place
  migration for existing databases.
- setup.ps1: pre-downloads the e5 model, smoke-tests the sqlite-vec load.
- Tests: `test_memory.py` (21 cases — prefixes, round-trip recall, dedup,
  Hinglish fact, forget, cadences, injection, suggestion, fallback,
  migration). Suite: 142 passing.

## Phase 1 — Text Agent, Real Tools & UI (2026-07-03)

- Safety gate (`core/safety.py`): deterministic DENY-first classifier with
  chain/scriptblock/subexpression extraction, encoded-command and IEX
  pre-checks; unknown commands default to CONFIRM. 60+ test cases.
- Confirmation flow: 60 s timeout → auto-NO; answerable from UI modal or CLI
  y/N prompt; kill switch cancels all pending.
- Event bus (`core/bus.py`): one emission path for every surface; audit rows
  written inline (durable), bus mirrors live.
- Tools: `get_system_stats` (psutil + NVML), `app_control`
  (Start-Menu index, WM_CLOSE→kill), `file_search` (Everything SDK IPC +
  scandir fallback), `read_file` (markitdown for pdf/docx), `write_file`
  (home-only), `run_shell` (gated PowerShell, UTF-8, 8 KB cap),
  `web_search` (ddgs), `fetch_page` (trafilatura).
- Audit log: every tool call → `audit_log` with class/approval/result.
- Web UI at 127.0.0.1:8765: streaming chat pane, live activity feed with
  safety-class colors, confirmation modal with countdown, header gauges
  (CPU/RAM/VRAM), Stop button. Vanilla JS, no build step.
- CLI rewired onto the bus; readiness sequence shared (`core/readiness.py`)
  with a "Baby ready" toast (winotify).
- setup.ps1: Everything install (winget) + SDK DLL download + autorun key.

## Phase 0 — Skeleton & Heartbeat (2026-07-03)

- Repo scaffold: uv-managed `pyproject.toml`, `config.yaml`, `.env.example`.
- SQLite store (`baby.db`, WAL) with full schema; conversation + message persistence.
- `ChatProvider` protocol + Ollama provider (OpenAI-compat, streaming, tool calls).
- Minimal `AgentCore` loop: message → model → tool → observe → reply, 8-iteration cap.
- Tool registry with `@tool` decorator (schema from type hints) + `get_time` dummy tool.
- CLI REPL (`python run.py --cli`) with streaming output and conversation resume.
- `scripts/setup.ps1`: Ollama install/check, model pull, env tuning, uv sync.
- Tests: agent loop with FakeProvider (tool threading, iteration cap, error recovery).
