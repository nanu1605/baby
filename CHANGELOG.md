# Changelog

## Unreleased — v2 conversational & reliable (feature/v2-conversational-reliability)

- P0 (2026-07-06): failing repros committed first — `tests/test_sensors.py`,
  `tests/test_db_hygiene.py`, `tests/test_loop_guard.py`. Version → `2.0.0-dev`.
- P1 (2026-07-06): sensors + tool contract (#6). New `get_sensors` tool reads
  CPU/GPU temps, fans and voltages from LibreHardwareMonitor over WMI, degrading
  to a structured `{error, hint}` when LHM is absent; `setup.ps1` installs +
  autostarts LHM (`wmi`/`pywin32` deps). `registry.dispatch` now wraps empty
  tool returns (`None`/`""`/`{}`) as an error, and the agent serves an honest
  audited line instead of the literal `"(no response)"` on empty output.
- P2 (2026-07-06): DB hygiene — never feed poison (#7). `messages` gains
  `turn_id` + `status`; a turn that errors is quarantined whole and never
  reloads; a hard-kill leftover (no assistant row) is failed at boot;
  `core/context.py::sanitize_messages` is a strict gate that makes every
  provider payload OpenAI-valid (orphaned tool rows, malformed args, empty
  content dropped and audited as `context_sanitizer`); a rejected context
  self-heals from the rolling summary and retries once.
  `scripts/migrate_v2_db.py` backs up, adds columns, quarantines existing poison.

## Unreleased — NIM cloud-primary migration (feature/nim-cloud-primary-router)

- N0 (2026-07-05): `core/providers/nvidia.py` — NVIDIA NIM provider on the
  same OpenAI wire as Ollama/Gemini (streaming, tool passthrough, 90 s
  cooldown on 429/5xx, cheap `healthy()`, network `probe()` with optional
  1-token generation ping). Config gains `models.nim_primary`/`nim_heavy`,
  the full `router:` cloud-primary block (inert) and `game_mode:`;
  `router.mode: local_primary` keeps behavior identical — `cloud_primary`
  is rejected until the N2 router lands. `.env.example` += `NVIDIA_API_KEY`.
  Acceptance one-off: `scripts/nim_smoke.py --model <catalog-id>`.
- N1 (2026-07-06): `scripts/pick_nim_model.py` shootout — T1–T9 battery ×5
  against Baby's real tool schemas, 36 RPM bucket (`core/ratelimit.py`),
  429 backoff, resumable caches, transcripts. Winners (Tanishq):
  `minimaxai/minimax-m2.7` primary (tools 100%, 1.4 s first token),
  `z-ai/glm-5.2` heavy (planning 5/5). Full data in
  `bench_results/REPORT.md`; rationale in DECISIONS.md #76.
- N2 (2026-07-06): Router v2 — `CloudRouter` + `HealthMonitor` in
  `core/router.py`, default `router.mode: cloud_primary`. Ladder per spec
  §2.1 (pins → offline → overflow → normal/heavy/game-mode), health state
  machine (1 failure → DEGRADED, DNS → OFFLINE, 3 probes + 1-token gen
  ping to recover, 90 s 429 cooldown, 45 s background probe), shared
  36 RPM token bucket (probes ride as background traffic), per-request
  mid-loop fallback resending the identical messages array, per-channel
  first-token timeouts (voice 3.5 s / text 8 s — AgentCore now passes
  `channel`), every transition/skip audited + on the activity feed.
  `/stats` gains `router.state` + `brain_turns`. Legacy `RouterProvider`
  untouched — `router.mode: local_primary` is the one-line rollback.
  Live-verified: boot green, real turn answered by minimax via the full
  stack (1.0 s first token), internal calls stayed local.
- N3 (2026-07-06): Pins + game mode. Privacy pins (`router.privacy_pins`,
  default `read_file`/`run_shell`) detected ROUTER-side: a pinned tool
  result in the context forces the rest of the turn local — outranking
  even game mode — and a redaction layer masks pinned bytes in any
  cloud-bound payload as defense-in-depth (capture-tested). Language pin:
  ≥30% Devanagari routes local (Roman Hinglish flows to NIM). Game mode:
  `set_game_mode` tool (safety ALLOW) + `POST /game_mode` — ON unloads
  the local 9B (~5.5 GB VRAM freed, all-cloud routing, honest failure if
  the net dies), OFF rewarns in the background and announces "Baby
  ready"; optional fullscreen auto-detect (`game_mode.auto_detect`,
  default off, `ui/gamewatch.py`). Live-verified VRAM swing via
  nvidia-smi.
- N4 (2026-07-06): Routing made visible + soak tooling. Per-message
  **brain badge** (local / NIM / Gemini, model + routing reason on
  hover — the brain that authored the final answer), header router-state
  dot (cloud/degraded/offline/game) and a game-mode toggle button.
  Router records a durable `served` audit row per completed stream
  (channel + first-token ms) — `scripts/soak_report.py` turns the audit
  trail into the PR soak summary (turns/brain, skip reasons, state
  transitions, first-token p50/p95, voice dead-air count, traceback
  count). `/stats` += per-brain `latency_ms` percentiles. README gains
  the cloud-primary architecture diagram.
- N5 (2026-07-06): **Local 35B removed** — `models.heavy`
  (qwen3.6:35b-a3b, ~20 GB on disk, >22 GB RAM to run) deleted from
  config and setup; its role is fully served by `nim_heavy`
  (z-ai/glm-5.2 on NIM). The one-line rollback
  (`router.mode: local_primary`) now escalates daily → Gemini only and
  is regression-tested without a heavy block.
- OR (2026-07-06): **Primary brain moved to OpenRouter** —
  `openai/gpt-4o-mini` (day-1 soak caught NIM serving 0/45 attempts;
  re-benched on OpenRouter: every test 100%, first token p50 1.2 s —
  see DECISIONS #83). Cloud slots gain `api_key_env` so primary
  (OpenRouter) and heavy (glm-5.2, still NIM) use separate hosts/keys;
  bench harness gains `--base-url/--key-env/--rpm/--tag`. Same-day
  hardening from the live E2E battery: shared-DB-connection lock
  (commit race), leaked `<think>`-tag scrub (reply/history/TTS/UI),
  empty replies always retried once, `POST /conversation/new` for
  clean scored runs, Playwright Ctrl+C teardown noise silenced.

## Phase 5 — Multi-Agent, Screen Awareness, Speaker Verification (2026-07-05)

- Multi-agent orchestrator (feature #9, `workers/orchestrator.py`): "start a
  project: build me a starter FastAPI app with auth and tests" → the best
  available brain (heavy if >22 GB RAM free, else Gemini, else daily with a
  notice — new router `tier_hint`) decomposes it into ≤4 independent
  subtasks on the tasks board; the Phase 4 worker pool executes them with
  per-worker progress in the feed; one integration call announces the
  result. Tools: `start_project` (gated like tasks), `project_status`,
  `cancel_project`; `GET /projects`; project lines in the activity feed.
- Screen awareness (+screen, `core/vision.py` + `describe_screen`): "what's
  on my screen?" → screenshot (primary monitor, downscaled to 1280 px JPEG)
  → the RESIDENT multimodal 9B — no second model, no eviction. Optional
  dedicated model via `screen.model` (unloads right after each call);
  Gemini vision fallback only when local fails, never silent, and
  `screen.allow_cloud_fallback: false` keeps screenshots on the machine.
- Speaker verification (+speaker-id, `voice/speaker.py`): sherpa-onnx CAM++
  embeddings (27 MB, CPU, ~ms) vs your enrolled voice
  (`scripts/enroll_voice.py`, 6 mixed EN/HI/Hinglish phrases, prints a
  suggested threshold). Unknown voices are CHAT-ONLY: every tool call is
  denied at the gate ("voice not recognized as the owner"), config
  `mode: ignore` drops them entirely. Kill phrases work for any voice;
  push-to-talk bypasses (keyboard = owner). Fail-soft: no enrollment/model
  → verification off, voice behaves exactly as Phase 4.
- docs/TAILSCALE.md: phone access to the UI over a tailnet-only HTTPS proxy
  (`tailscale serve`), localhost bind unchanged; explicit "never Funnel".
- DB: `projects` table + `tasks.project_id` (in-place migration).
- Deps: sherpa-onnx (the only new one — vision and capture ride on
  existing deps). setup.ps1 §3e downloads the speaker model.
- Tests: +63 (router tier_hint, project CRUD/migration, orchestrator
  lifecycle matrix, vision chain, speaker verify + fail-soft, gate
  enforcement, UI surfaces). Suite: 375 passing.

## Phase 4 fixes — owner testing feedback (2026-07-05)

- Background task finished with "(no response)": after a long tool loop the
  thinking model can burn its whole generation window reasoning and return
  empty content. The agent now retries once with thinking disabled and no
  tools, forcing a plain answer from the tool results already gathered.
- Baby said "asterisk asterisk": spoken text now passes `strip_markdown`
  inside TTS synth (bold/italic/code/links/headings/bullets removed) — one
  funnel covers replies, announcements, and the briefing.
- "ollama" heard as "ullama": Whisper now gets a `hotwords` decoder bias
  (`voice.stt.hotwords` — Baby, Ollama, Telegram, Gemini, lakh, …) on every
  decoding window.
- Browser never opened ("playwright not installed"): the Chromium binary
  was missing — `uv run playwright install chromium` ran; setup.ps1 §3d
  already covers fresh machines.
- New: system tray icon (pystray) — green ready, amber working, red waiting
  on a confirmation; menu Open Baby / Quit Baby. `tray.enabled: false`
  turns it off.
- Tests: +17 (finalize retry, markdown strip, hotword passthrough, tray
  state). Suite: 295 passing.
- Browser stuck after closing the window: the dead BrowserContext was kept
  and every action failed with "Target page, context or browser has been
  closed" until restart. A dead context now relaunches cleanly, and an
  action interrupted by a window close resets state so the retry reopens
  the browser. Verified against real Chromium (goto/read/screenshot/
  relaunch). Suite: 298 passing.
- "Open google.com and search X" always failed: the model omits the
  selector argument ('type needs a selector' killed every search, and no
  key-press action existed to submit one). browser_act now finds the
  search box itself when type has no selector, accepts click selectors in
  either slot, gains a `press` action (Enter submits — same per-domain
  confirm as click/type), and its description teaches the reliable path:
  goto google.com/search?q=… then read. Verified live both ways.
  Suite: 305 passing.
- Model kept saying "Playwright binaries are not set up" AFTER the fixes,
  without calling the tool: it was imitating its own old failure excuses
  in the conversation history. The per-turn trailing nudge now tells it to
  ignore earlier broken-tool claims and trust only the current tool list;
  the stale excuse messages in baby.db were replaced with a corrective
  note.
- Stop button mid-turn made the NEXT turn answer the stopped command: the
  bare "(cancelled)" marker read as an unanswered question. The marker now
  says the request was abandoned and must not be resumed.
- "I'll open Yahoo and run that search for you." — full stop, no tool
  call, owner repeats himself: a promise-shaped reply with zero tool calls
  now gets one deterministic push to actually call the tool. Suite: 309
  passing.
- Voice refused screenshots that worked when typed: the voice
  conversation's rolling summary had baked in "Playwright binary errors
  remain unresolved" and is injected as context every turn. The summarizer
  now drops broken-tool claims (also when inherited from the previous
  summary), the per-turn nudge covers the summary too, and the poisoned
  summary/messages in baby.db were cleaned.
- autostart.ps1 failed to parse (PS 5.1 reads BOM-less UTF-8 as ANSI; an
  em dash became a curly quote that terminated the string) — both scripts
  are now pure ASCII.
- Autostart ran but Baby died at logon: it boots faster than the Ollama
  app ("Ollama is not reachable"). ready_check now starts `ollama serve`
  itself and waits up to `startup.wait_for_model_s` (120 s) for the
  daemon; the scheduled task also retries 3× a minute apart. Suite: 312
  passing.

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
