# Decisions Log

Running log of non-obvious choices made during the build. Newest last.

## Phase 0 (2026-07-03)

1. **Daily model tag**: spec's `qwen3.5:9b-instruct-q4_K_M` does not exist in the
   Ollama library; the published quant tag is `qwen3.5:9b-q4_K_M` (6.6 GB, no
   `-instruct` suffix). Config updated accordingly.
2. **VRAM watch**: the 9B Q4_K_M weights file is 6.6 GB vs the plan's ~5.5 GB
   estimate (weights + 8K ctx). May pressure the 8 GB budget once Whisper loads
   in Phase 3 — re-measure then; fallbacks (Whisper→CPU, ctx→6144) already
   documented in the plan.
3. **num_ctx**: Ollama's OpenAI-compatible endpoint ignores per-request `num_ctx`,
   so context length is set globally via the `OLLAMA_CONTEXT_LENGTH=8192` user env
   var in `setup.ps1`. `keep_alive` IS honored per-request via `extra_body`.
4. **sqlite-vec table deferred**: `fact_vectors` (vec0 virtual table) is omitted
   from `schema.sql` until Phase 2 — creating it requires the sqlite-vec extension
   loaded, which isn't a dependency yet; including it would break boot.
5. **History reload policy**: on restart, only `user`/`assistant` text messages are
   replayed into model context. Raw `tool` rows are persisted for audit but not
   replayed (OpenAI format requires pairing them with `tool_calls` ids, which adds
   complexity for no conversational value).
6. **bus.py deferred to Phase 1**: with only the CLI client, an event bus is
   indirection with one subscriber. CLI calls `AgentCore.run_turn()` directly with
   callbacks; the bus arrives with the UI's websocket activity feed.
7. **uv installed user-scope** (`~\.local\bin`) via the official installer — no
   admin elevation, not in the spec's "ask first" list of system installs.
8. **Python 3.13**: machine has 3.13.14; spec floor is 3.11+. Using what's
   installed rather than pinning an older interpreter.
9. **Ollama /v1 ignores `options.num_ctx`** — verified empirically (model loaded
   at 4096 despite the option). `OLLAMA_CONTEXT_LENGTH=8192` (user env var +
   Ollama restart) is the working mechanism; done on this machine on 2026-07-03
   and probe confirmed `context_length: 8192`. The CLI readiness sequence now
   warms the model and warns if the served context is below `num_ctx`.
10. **setup.ps1 avoids parsing `ollama list`**: in PowerShell 5.1 with
    `$ErrorActionPreference = "Stop"`, redirected native stderr becomes a
    terminating NativeCommandError (reproduced). Model presence is checked via
    `GET /api/tags` instead, and the winget branch patches the session PATH
    (winget only updates the registry PATH).
11. **History window filters roles in SQL**: `get_messages(..., roles=...)`
    filters `tool` rows in the query, not client-side — otherwise tool rows eat
    LIMIT slots and conversation memory silently shrinks on tool-heavy turns.
12. **Sync tools run via `asyncio.to_thread`** in registry dispatch so a future
    blocking tool (search, subprocess) can't stall streaming or other channels.

## Phase 1 (2026-07-03)

13. **`duckduckgo-search` → `ddgs`**: the spec-named package was frozen and
    renamed in July 2025; using `ddgs`.
14. **`pynvml` → `nvidia-ml-py`**: the `pynvml` PyPI package is deprecated;
    `nvidia-ml-py` is NVIDIA's official binding (still imports as `pynvml`).
15. **Everything64.dll downloaded, not vendored**: `setup.ps1` pulls the
    voidtools SDK zip and drops the DLL in `%LOCALAPPDATA%\baby\`; the binary
    stays out of git.
16. **.lnk resolution without pywin32**: one PowerShell `WScript.Shell` COM
    script per index build emits JSON; `open` launches the `.lnk` itself via
    `os.startfile` — no binary .lnk parsing on the hot path.
17. **"taskkill after 5 s" implemented as `psutil.Process.kill()`** — same
    Win32 `TerminateProcess`, one less subprocess.
18. **`Stop-Computer`/`shutdown` DENY→CONFIRM downgrade** uses a deterministic
    shutdown-intent regex over the triggering user message — the LLM still
    never classifies its own commands.
19. **Chain splitting is quote-unaware by design**; mis-splits inside quotes
    over-restrict (unknown → CONFIRM), never fail open. Scriptblock `{...}`
    and subexpression `$(...)` contents are extracted as extra segments so
    `ForEach-Object { Stop-Process lsass }` can't hide inside a pipeline.
20. **Callbacks replaced by the event bus**: one emission path for CLI, UI,
    and future surfaces. Audit rows are written inline in the dispatch path —
    the bus (drop-oldest under backpressure) is only the live mirror.
21. **markitdown installed with `[pdf,docx]` extras** — read_file converts
    pdf/docx/pptx/xlsx without the full dependency kitchen sink.
22. **Kill switch scope in Phase 1**: UI Stop button + `POST /kill` (cancels
    the turn task and all pending confirmations). Global hotkey and voice
    phrases arrive in Phase 3 with the keyboard/voice threads.
23. **Fallback file index uses a 24 h TTL checked on use**; the spec's nightly
    rebuild belongs to Phase 4's scheduler.
24. **`GET /tasks` and `GET /memory` omitted** — their data layers are
    Phase 4/Phase 2 respectively.
25. **`cd`/`Set-Location` special-cased ALLOW** despite the generic
    `Set-*` → CONFIRM rule; it mutates nothing on disk.
26. **`GET /stats` endpoint added** for the header gauges (spec says "polled"
    without naming a route).
27. **Everything must run non-elevated**: the winget installer launched it
    elevated and SDK IPC failed (error 2) until restarted normally. setup.ps1
    adds an HKCU Run key (`-startup`) so it starts per-login, non-elevated.
28. **UI multi-socket flows verified manually**: Starlette's TestClient gives
    each websocket session its own portal thread, and asyncio queues are not
    thread-safe across portals — the confirm-modal round trip is covered by
    agent-level tests plus the manual checklist.
29. **`explorer` not in the deny kill set** (spec lists only core session
    processes); killing it falls to the generic taskkill CONFIRM, since
    restarting Explorer is a legitimate ask.

## Phase 2 (2026-07-03)

30. **sqlite-vec pinned `>=0.1.9,<0.2`**; the vec0 DDL lives in
    `memory/store.py`, not `schema.sql` — the extension loads per-connection,
    and every other consumer of the DB (including all Phase 0/1 tests) must
    keep working without it. 0.1.7+ is required for reliable vec0 DELETE.
31. **`forget()` deletes the vector row** (and only flips `facts.active=0`,
    keeping the row for audit): a vec0 KNN `MATCH` runs before any JOIN/WHERE
    on other tables (sqlite-vec #196), so `active=1` cannot filter the KNN
    pass — dead vectors would hold top-k slots forever. `search()` still
    over-fetches 3x and join-filters as a backstop.
32. **`min_similarity: 0.80`** — e5 cosine scores are anisotropic (unrelated
    pairs land ~0.70–0.75), so the spec's "similarity floor" defaults to 0.80
    and is exposed in config for tuning. Unit tests use a token-bag
    FakeEmbedder whose scores sit far lower, with a 0.1 floor — they test the
    floor's semantics, not its calibration.
33. **Feature #8 is one dedicated post-task call** (`tools=None`, 80-token
    cap, streamed with a `Next:` prefix); Appendix A's "proactively suggest
    the next step" persona line is omitted — both together produce double
    suggestions. The extra call only fires when at least one tool succeeded,
    and its failure never fails the turn.
34. **qwen3.5 is a thinking model on Ollama**: `max_tokens` is consumed by the
    `reasoning` channel first, so tightly-capped calls returned empty content.
    Verified `reasoning_effort: "none"` is the only /v1 knob that disables
    thinking (`think: false` and `chat_template_kwargs` are ignored). All
    internal capped calls (summary, extraction, next-step) pass it.
35. **Brute-force fallback ships dormant**: if vec0 ever fails to load,
    the store keeps float32 BLOBs in a plain `fact_embeddings` table and does
    Python cosine over them — same public API, exercised by a forced-failure
    test. On this machine the extension loads (verified: CPython 3.13.14,
    SQLite 3.50.4, win_amd64 wheel).
36. **Extractor has its own watermark** (`conversations.extracted_upto`) and
    advances it even when the model's JSON is unparseable — one extraction
    attempt per message span, never a retry loop on the same text.
37. **Persona hardened after a live fabrication**: the 9B model replied
    "locked in memory" without calling `remember` (nothing stored). The
    memory section now says it MUST call the tool and that nothing is stored
    without it — verified fixed live.
38. **Embeddings via sentence-transformers on CPU**: PyPI torch wheels on
    Windows are CPU-only (the 117 MB download confirms it), so the GPU budget
    stays untouched. fastembed/ONNX noted as plan B if torch becomes a burden.
39. **Forgotten facts keep their vectors** (reverses #31's vector delete):
    owner testing caught the extractor re-inserting a just-forgotten fact —
    with the vector gone, dedup couldn't see it. Now `forget` only flips
    `active=0`; dedup checks inactive matches and (a) drops extracted
    near-matches of forgotten facts at the `min_similarity` threshold (looser,
    since extracted phrasing rarely matches word-for-word), (b) reactivates on
    an explicit re-remember. `forget` also deactivates ALL matches above the
    floor, not just the best one — a paraphrased duplicate had slipped past
    dedup and kept answering after its twin was forgotten.
40. **Language is pinned per turn, deterministically**: the persona's
    "reply in the user's language" rule alone doesn't hold — with a
    Hinglish-heavy history the 9B answered English questions in Hinglish.
    `detect_language()` (Devanagari check + Roman-Hindi marker words) pins the
    reply language in the system prompt AND in a trailing system nudge next to
    generation; the trailing position is what actually works.
41. **Tone mirrors the current message**: persona + trailing nudge instruct
    professional/emoji-free replies for work or serious questions, playful
    only for casual chat (owner feedback: Baby was jokey about serious tasks).
    Also: the model started imitating "Next:" lines it saw in history — the
    suggestion call is now skipped when the reply already contains one.

## Phase 3 (2026-07-04)

42. **Whisper runs on CPU, not cuda** (owner-approved spec deviation): the 9B
    model measures 7.99/8.0 GB VRAM warm, and CTranslate2 disables INT8 CUDA
    kernels on sm_120 (RTX 50-series, `CUBLAS_STATUS_NOT_SUPPORTED`), so GPU
    Whisper would need float16 ≈ 2 GB that doesn't exist. CPU int8
    `large-v3-turbo` on 8 threads measures ~5.5 s/utterance (the 30 s window
    padding dominates); `small` does 1.4 s but degrades Hindi — turbo kept,
    `voice.stt.model: small` documented as the speed knob.
43. **`kokoro-onnx` instead of the official `kokoro` pip** (owner-approved):
    `kokoro` 0.9.4 and its `misaki` G2P pin Python `<3.13`; this venv is
    3.13. `kokoro-onnx` 0.5.0 loads the same Kokoro-82M v1.0 weights
    (`kokoro-v1.0.onnx` + `voices-v1.0.bin`) via onnxruntime CPU. Bonus: its
    `espeakng-loader` wheel bundles espeak-ng, so the spec's system-level
    espeak-ng install became a verify-only step — nothing to install.
44. **`hey_jarvis` interim wake word** (owner-approved): openWakeWord's
    training tools are not Python-3.13-clean and local training would fight
    the LLM for GPU, so the owner trains `hey_baby.onnx` on Colab
    (`scripts/wakeword_training.md`). The pretrained `hey_jarvis` model ships
    as fallback; dropping `models/hey_baby.onnx` in place switches
    automatically at next boot — no config change.
45. **Push-to-talk via ctypes `RegisterHotKey`**, not the `keyboard` package
    (unmaintained, global-hook based): a `GetMessageW` pump on a daemon
    thread, no admin needed, `WM_QUIT` via `PostThreadMessageW` to stop.
    Registration failure (combo taken) degrades to wake-word-only with a
    warning.
46. **Voice never confirms**: a `confirm_request` on the voice channel is
    spoken as "check the screen" and resolved only in the UI modal. Yes/no
    by voice would make the deterministic gate's approval channel spoofable
    by anything the mic hears (TV, other people).
47. **Cross-thread contract**: `EventBus.publish` is `put_nowait` and stays
    LOOP-THREAD-ONLY. Voice thread → loop via
    `asyncio.run_coroutine_threadsafe(agent.run_turn(...), loop)` and
    `loop.call_soon_threadsafe(functools.partial(bus.publish, ...))`
    (`call_soon_threadsafe` accepts no kwargs); loop → voice thread via a
    stdlib `queue.Queue` fed by a bridge coroutine; cancel is `fut.cancel()`.
48. **VRAM acceptance amended** (owner-approved): the spec's "≤7.5 GB with
    9B + Whisper" line is unmeetable — the 9B alone holds 7.99 GB. New
    criterion: the voice stack adds **0 VRAM** (everything CPU) and Ollama is
    never evicted. Verified live: 8.04 GB during a voice boot, all Ollama.
49. **Mic input stream never closes** while the pipeline runs — barge-in
    detection during playback depends on it. Playback writes ~100 ms chunks
    checking a stop flag, so interruption lands within a beat. No AEC:
    open-speaker echo is handled by raising `barge_in_threshold` or a
    headset (documented in the checklist).
50. **VAD endpointing needs a speech-start gate** (owner bug: "replied only
    the first time"): silence was counted from the first captured frame, so
    pausing >400 ms after the beep ended the capture before the user spoke —
    whisper then saw pure silence and the turn vanished without feedback.
    The first try worked only because wake word + question were said in one
    breath (DB transcript "time is it." — the lead chopped by the beep).
    Now silence ends an utterance only after speech has been seen;
    pre-speech quiet gets `vad_speech_wait_ms` (5 s) grace, silent captures
    skip STT, and the feed says "voice: heard nothing".

## Phase 4 (2026-07-04)

51. **Router is a provider, not an agent feature**: `RouterProvider`
    implements the same ChatProvider protocol AgentCore consumes, wrapping
    {daily, heavy, cloud} — zero agent changes, and every surface (UI,
    voice, tasks, scheduler, telegram) inherits escalation for free.
    Per-turn stickiness keys on the trailing user message (stable across a
    turn's tool-loop iterations); internal capped calls (max_tokens set, no
    tools) always stay on daily. `retry_after_failure` arms via a duck-typed
    `record_turn_result` hook called from run_turn's finally.
52. **`long_context` escalates to CLOUD, never heavy**:
    `OLLAMA_CONTEXT_LENGTH` is global, so the 35B gets the same 8K window as
    the 9B — swapping 24 GB of weights for zero context gain would be pure
    loss. Also: heavy needs >22 GB free RAM (usually NOT available on this
    32 GB box) — the deny-and-fall-through path is the common case and every
    denial is published ("heavy denied: 9.8 GB free < 22").
53. **Gemini via its OpenAI-compat endpoint** (`…/v1beta/openai/`) with the
    same `openai` AsyncClient as Ollama — no google SDK. The fragmented
    tool-call stream accumulation moved to a shared
    `base.accumulate_stream()`. 429/5xx puts the provider in a 5-minute
    cooldown the router reads; cloud is never a hard dependency.
54. **Completion notifications are direct calls, not bus events**: the bus
    is drop-oldest under backpressure by design — a "task done"
    toast/telegram must not be droppable. task_queued/started/done events
    are published ADDITIONALLY, for the activity feed only.
55. **Voice announcements get their own queue** (`announce_q`, maxsize 5),
    drained at the top of `_idle()` — `sentence_q` is only consumed in
    RESPONDING, so queuing there would speak announcements into the next
    conversation. TTS synth runs on the voice thread (never the loop);
    a live conversation always wins; overflow drops with a status line.
56. **Browser per-domain confirm state lives in the SafetyGate**
    (`SafetySession`), and the domain comes from the REAL Playwright page
    via an injected `current_domain` callable — model kwargs are never
    trusted for it, so the LLM can't claim a pre-approved domain to skip
    the confirm. `gate.note_approval` records the domain only after the
    human clicked Yes. The set resets per process (per spec: per session).
57. **`start_background_task` is gated by a destructive-intent keyword scan**
    over title+spec (delete/uninstall/pay/send/…, plus Hinglish forms):
    routing natural-language specs through `classify_shell` would CONFIRM
    every benign research task (unknown→CONFIRM). Tools inside the running
    task still pass the gate individually — defense in depth.
58. **Task channel is `task:{id}`**: free-text column, gives task_events
    attribution and UI grouping with no schema change, and can never collide
    with the voice bridge's `channel == "voice"` filter. Background/scheduler
    agents run with `memory=None, suggest_next_step=False, max_iterations=15`
    — one-shot conversations don't need rolling summaries, and N concurrent
    maintenance calls into the 9B would serialize everything.
59. **APScheduler pinned `>=3.11,<4`** — 4.x is still pre-release. Briefing
    is agent-composed (one prompt from `briefing.include` through a
    scheduler-channel AgentCore) instead of bespoke aggregation code: it
    reuses the already-tested tools. `misfire_grace_time=3600` fires an
    08:00 briefing on a PC that wakes at 08:40 (owner choice).
60. **python-telegram-bot v22 embedded manually**: `run_polling()` blocks
    and owns the loop, so the bot drives initialize → start →
    `updater.start_polling` inside the existing loop (reverse on stop).
    Chat-id lockdown per spec §18; confirmations surface as inline Yes/No
    whose callback resolves the SAME ConfirmationManager as the web modal
    (owner-approved: the phone may approve gated actions).
61. **Autostart = current-user Task Scheduler job running pythonw.exe**
    (`-Hidden`, `-StartWhenAvailable`, no time limit, `-Force` idempotent,
    no admin). Task Scheduler can't redirect output, so run.py detects
    None streams and self-logs to `%LOCALAPPDATA%\baby\logs\baby.log`.
    Registered LAST per the spec's "autostart hides a crashing app" risk;
    `autostart.ps1 -Remove` unregisters.
62. **Atomic task claim via `UPDATE … RETURNING`** on the single aiosqlite
    connection — two workers can never grab the same row, no polling races.
    `cancel_task` cancels the per-task child asyncio.Task; the worker loop
    survives and moves on.
63. **Empty final reply → one forced retry with thinking off** (owner bug:
    first background task ended "(no response)"): after a long tool loop
    the qwen thinking model can spend its whole generation window in the
    reasoning channel and emit zero content. When the closing call returns
    empty text after ≥1 successful tool, `_final_answer` re-asks once with
    `tools=None, max_tokens=700, reasoning_effort="none"` — the router
    treats it as an internal call (stays daily) and the tokens still stream
    to the UI/voice.
64. **Markdown is stripped inside `TextToSpeech.synth`** (owner bug: Baby
    said "asterisk asterisk"): synth is the single funnel every spoken
    sentence passes — replies, announcements, briefing, prerender — so one
    `strip_markdown` there beats patching each call site. A chunk that is
    pure markdown (a lone `**`) synthesizes to zero samples instead of
    crashing Kokoro.
65. **Whisper `hotwords` over `initial_prompt`** for accent bias ("ollama" →
    "ullama"): hotwords bias the decoder on EVERY window, while
    initial_prompt only conditions the first and is a known hallucination
    echo vector. The list lives in `voice.stt.hotwords` (config), empty
    string disables.
66. **Tray icon on a pystray thread** (owner request): green ready / amber
    working / red waiting-on-confirm, menu Open Baby + Quit Baby. Same
    narrow thread exception class as the toast helper — the tray thread
    never touches Baby's state; a bus-subscriber coroutine folds events
    (`TrayState`) and pushes image/tooltip updates in. Quit hops back to
    the loop via `call_soon_threadsafe` and stops uvicorn cleanly.
67. **The daily 9B is the vision brain** (Phase 5 headline finding): the
    Ollama qwen3.5 family is multimodal — the RESIDENT model answers
    "what's on my screen?" with zero eviction and zero reload. A dedicated
    model (`screen.model: qwen3-vl:2b-instruct`, 1.9 GB) stays as the
    config escape hatch, sent with per-request `keep_alive: "0s"` so it
    unloads immediately (its eviction of the 9B is opt-in, owner-accepted).
68. **VisionService owns its own local→Gemini chain, NOT RouterProvider**:
    the router's failover would try the 24 GB heavy model on a vision
    failure — exactly the wrong reflex. It reuses the router's
    GeminiProvider instance so the 5-minute 429 cooldown state is shared.
    Screenshots can contain secrets: the cloud path fires only when local
    vision fails AND `screen.allow_cloud_fallback` is true, and it is never
    silent — a status event announces the screenshot leaving the machine.
69. **Speaker verification = sherpa-onnx CAM++ (27 MB onnx, CPU)**:
    pip-installable cp313 Windows wheels, self-contained runtime, tens of
    ms per utterance. SpeechBrain rejected (no Windows support),
    Resemblyzer rejected (unmaintained since 2023). Profile is plain JSON
    (`models/owner_voice.json`) — inspectable, no pickle. Enrollment mixes
    EN/HI/Hinglish phrases because CAM++ is VoxCeleb-English-trained; the
    script prints the intra-speaker similarity matrix and a suggested
    threshold instead of pretending one number fits every mic and room.
70. **Unverified-voice enforcement is gate-level and channel-scoped**:
    `SafetySession.unverified_channels` + a `channel` param on
    classify_tool DENY every tool on an unverified voice turn ("chat
    only") — the LLM cannot bypass a gate verdict, and concurrent UI/task
    turns keep their tools. Ordering is a safety statement: kill phrases
    are honored for ANY voice, THEN verification. PTT bypasses it (whoever
    pressed the hotkey owns the PC). A broken check fails OPEN with a loud
    error event — locking the owner out is worse than one missed check,
    and gated actions still require the on-screen confirm anyway.
71. **Orchestrator forces the smart brain via `tier_hint="best"`**, a new
    chat opt the router honors BEFORE the internal-call short-circuit and
    the sticky cache, reusing the same candidate walk (RAM gate, health,
    denial audits, /stats badge all keep working). Heavy and cloud both
    denied → daily with the denial notice, per spec.
72. **The orchestrator never touches tools** — planning and integration are
    capped no-tools chat calls; only the WorkerPool's daily-model workers
    act (and every worker tool call still passes the gate). One project at
    a time, ≤4 independent disjoint-file subtasks, one corrective JSON
    re-ask, bus-completion with DB-poll fallback, hard timeout: small local
    models compound errors across chained steps, so chains stay short and
    failures are clean, never hung. Workers all share the one 9B — Ollama
    serializes generation, which is the real VRAM bound (spec §15).
73. **Tailscale Serve, never Funnel** for phone access: tailnet-only HTTPS
    proxy onto the localhost bind, zero code change. Funnel would publish
    a login-less UI (with the confirm modal!) to the open internet.
74. **NIM migration runs on a feature branch behind `router.mode`** (change
    spec NIM_MIGRATION_PLAN.md): the cloud-primary brain hierarchy lands
    phase by phase on `feature/nim-cloud-primary-router` with a draft PR,
    and the old local-primary behavior stays selectable — rollback is one
    config line at any point, even post-merge. In N0 `cloud_primary` fails
    loud (ValueError) instead of silently running the legacy ladder under
    a config that promises different routing; the N2 router replaces it.
75. **NvidiaProvider keeps `healthy()` cheap and puts the network probe in
    `probe()`** — the router consults healthy() on every pick, so it must
    not hit the wire; the 45 s background probe (Phase N2) owns the
    models-list GET, and the 1-token generation ping runs only on the
    DEGRADED→CLOUD recovery attempt to avoid burning free-tier quota.
    reasoning_effort is not forwarded to NIM until the N1 bench measures
    per-model unknown-parameter tolerance.
76. **NIM winners (Tanishq, 2026-07-06): minimaxai/minimax-m2.7 primary,
    z-ai/glm-5.2 heavy** - from the N1 shootout (bench_results/REPORT.md,
    T1-T9 x5 against Baby's real tool schemas, evening IST). minimax:
    perfect tool calling and 1.4 s first token (under the 3.5 s voice
    cutoff); its weaknesses - 60% error honesty, rejects reasoning_effort,
    0% planning JSON - do not touch the primary slot (internal capped
    calls stay local; planning is the heavy slot's job). glm-5.2: 5/5
    valid planning decompositions and 100% on every quality test except
    error honesty; ~130 s first token is acceptable only because heavy
    work is background (orchestrator timeout 1800 s). Disqualified:
    mistral-nemotron (emits pseudo-code instead of native tool_calls),
    llama-4-maverick (broken on NIM serverless, 79 s first token),
    kimi-k2.6 (chronic 429 wall on the free tier, 80 rate-hits even
    off-peak), nemotron-3-super (claims success on failed tools, T4 0%).
    Bench fidelity note: T9 runs with tools=None exactly like the
    orchestrator plan call - with tools attached, models called
    start_project instead of emitting JSON.
77. **Router v2 keeps three deliberate deviations from a literal spec read**:
    (a) healthy() on the NIM provider stays cheap (key + cooldown check) and
    the network probing lives in probe()/HealthMonitor - the ladder consults
    health on every pick and must not hit the wire; (b) a real successful
    NIM call counts toward recovery exactly like a probe (spec: "successes
    count toward recovery") and a full generation IS the generation proof,
    so the 1-token ping is only spent when recovery rides probes alone;
    (c) internal capped calls (summary/extraction/suggestion) stay on the
    warm local 9B even in cloud_primary - free, private, immune to cloud
    hiccups - EXCEPT tier_hint="best" (orchestrator planning/integration),
    which outranks the short-circuit because those calls are capped and
    toolless by design yet want the best brain. Text-based heavy triggers
    are not consulted for internal calls: a summary containing the word
    "plan" must not wake the heavy brain.
78. **Overflow skips the cloud entirely, backstop included**: when the token
    bucket is empty the ladder jumps straight to local (spec 2.1 "skip cloud
    entirely; no queueing") - Gemini is not a rate-limit escape valve, it is
    a failure backstop. In game mode (local unloaded) overflow is an honest
    error instead.
79. **Privacy pins live in the ROUTER, not the agent**: the router already
    sees the full messages array on every call, so it detects pinned tool
    results in context (tool_call_id -> name map from the assistant
    entries) and forces the rest of the turn local - zero agent surgery,
    and the pin can never be forgotten by a future agent refactor. Pins
    outrank game mode: private bytes never go cloud even if that means
    reloading the unloaded local brain. Redaction of pinned bytes in
    cloud-bound payloads stays as defense-in-depth (tool results never
    reload into later turns - roles filter - so in-turn is the only
    exposure window).
80. **Game mode is a plain ALLOW tool** (set_game_mode): VRAM juggling is
    reversible and touches no data, so gating it would only train the
    owner to click through confirmations. The fullscreen auto-detect
    watcher only reverses toggles IT made - a manual "game mode on" is
    never fought when the owner alt-tabs.
81. **The brain badge shows the FINAL answer's brain**, snapshotted at
    turn_end publish before the maintenance task's internal calls
    overwrite router.active. A multi-rung turn (fallbacks mid-loop) is
    still summarized by whoever wrote the words the owner read.
82. **Soak metrics live in audit_log, not a new store**: the router
    already audits every route/skip/state decision; N4 adds one durable
    "served" row per completed stream (channel + first-token ms). The
    soak report is a pure read over that trail - restart-proof, no
    schema change, and the same rows double as the live activity feed's
    forensic record (they solved the silent-voice-turn bug in minutes).
83. **Primary brain moved from NIM to OpenRouter (openai/gpt-4o-mini)**:
    day-1 soak showed NIM serving 0 of 45 routed attempts - congested
    the whole window, so cloud-primary delivered no cloud (the ladder
    hid it; feel stayed local). Re-benched on OpenRouter with the same
    T1-T8 harness (bench_results/openrouter/): gpt-4o-mini 95.2 (every
    test 100%, first token p50 1.2 s / p95 4.0 s), deepseek-chat-v3.1
    90.5, qwen3-32b 73.3, llama-3.3-70b 22.3, and minimaxai/minimax-m2.7
    - the NIM winner - scored 0 through OpenRouter (200/200 stream
    errors with tools; fine on plain requests, broken through its
    provider pool). Heavy stays z-ai/glm-5.2 on NIM: background-only,
    tolerates congestion, planning benched 5/5. Slots carry api_key_env
    so each can point at a different host; tier names (nim_primary)
    kept for audit/report continuity. NIM rollback is three config
    lines (recorded inline in config.yaml). Tanishq picked the winner.
84. **Local 35B removed (N5)**: qwen3.6:35b-a3b deleted from config,
    setup.ps1 and disk (~20 GB reclaimed; `ollama pull qwen3.6:35b-a3b`
    brings it back if ever wanted). Its every trigger (tier_hint,
    planning, retry, long context) is served by nim_heavy - z-ai/glm-5.2
    on NIM, which benched 5/5 on planning and tolerates congestion
    because it is background-only. The legacy local_primary rollback
    keeps working without a heavy block: build_provider only wires heavy
    when models.heavy.model exists, so rollback = daily + Gemini cloud
    (regression-tested). The RAM-gate machinery in RouterProvider stays
    (harmless, exercised by tests) for anyone who re-adds a local heavy.

## v2 — Conversational & Reliable (2026-07-06)

85. **CPU temperature comes from LibreHardwareMonitor's HTTP web server,
    not WMI or pythonnet (P1)**: psutil reads no temps on Windows
    (Linux-only API). The first cut used LHM's WMI provider
    (`root\LibreHardwareMonitor`), but LHM dropped WMI in the 0.9.x line
    (owner hit it live), so get_sensors now GETs LHM's Remote Web Server
    JSON (`http://127.0.0.1:8085/data.json`, `LHM_URL`-overridable) and
    walks the node tree by unit suffix (°C / RPM / V). This uses the
    already-present `httpx` and drops the `wmi`/`pywin32` deps. The
    rejected alternative — loading `LibreHardwareMonitorLib.dll`
    in-process with pythonnet — avoids the extra process but forces the
    whole of Baby to run elevated (the sensor driver needs admin), a far
    larger blast radius than an LHM the user runs elevated once. LHM runs
    minimized at login (setup.ps1 3f) with the web server on; when it is
    absent get_sensors returns a structured `{"error", "hint"}` naming the
    fix instead of nothing. GPU temp/power stays on pynvml (already present).
86. **The tool contract is enforced once, in dispatch (P1)**: a tool
    returning `None` / `""` / `{}` used to read as success — agent.py
    counts any result not prefixed `{"error"` as a win — so silence
    reached the user as "(no response)". `registry._finalize` now wraps
    those as `{"error": "tool returned no data"}` for the whole tool
    surface; non-empty lists (a legitimate "no matches" → `[]`) pass
    through as data. Paired with the loop guard: an empty generation,
    even after the one _final_answer retry, is served as an honest
    "try that once more?" line and audited (tool="generation"), never
    the bare placeholder. The literal "(no response)" is gone.
87. **DB hygiene: tag-and-quarantine per turn, not one held transaction
    (P2)**: the spec asked for "transactional turns", but a transaction
    held open across a whole run_turn would re-open the exact
    shared-connection hazard the db.lock was added to close — a commit from
    a background coroutine landing in the turn's read gap. Instead every row
    of a turn shares a `turn_id`; a turn that errors is marked
    `status='failed'` in one UPDATE (excluded from every context load), a
    cancelled turn keeps its closure marker (NOT quarantined, so later
    turns do not re-answer it), and a hard-kill leftover (a turn with no
    assistant row) is failed at boot by `_reconcile_incomplete_turns`.
    Legacy rows (turn_id NULL, pre-P2) are never touched — the ALTER
    default backfills them 'ok', so no history is lost. Three more layers
    back this: `core/context.py::sanitize_messages` is applied to every
    assembled array (drops orphaned tool rows, repairs malformed tool-call
    args to `{}`, drops empty content — audited as context_sanitizer, and
    idempotent so it never mangles a valid multi-tool turn); a provider
    that still 4xx-rejects the context self-heals once from the rolling
    summary + current turn; and `scripts/migrate_v2_db.py` sweeps the
    existing DB. The sanitizer running on every assembly is what makes
    persistent poison impossible — the next turn is already clean, so the
    same debris can never re-break a call.
88. **Conversation mode + proceed/cancel (P3)**: three choices. (a) The
    follow-up window reuses the existing LISTENING state with a
    `source="followup"` flag rather than a new state — after playback's
    `None` sentinel the pipeline calls `_enter_listening(source="followup")`
    (soft cue, no wake beep), and `_listen` uses `conversation.window_s` as
    the no-speech timeout and checks `is_end_phrase`. The window opens only
    AFTER playback fully ends and drains the mic + resets VAD, so Baby never
    transcribes its own speech. Default-on, one flag (`conversation.enabled`)
    to disable. (b) Proceed/cancel is "approach A": a one-shot
    `AgentCore.pending_suggestion` armed when a turn offers a next step; a
    short "yes" re-submits it as a normal turn with a system nudge (so tools
    STILL pass the safety gate — approve-to-proceed never bypasses a
    CONFIRM), a short "no" acks with no model call, anything else expires it
    and runs as a fresh turn. Not a structured tool-exec — reusing the loop
    keeps the gate authoritative. (c) `core/intents.py` is the single
    multilingual yes/no source (EN/HI/Hinglish), replacing the inline
    `clients/cli.py` set; it matches only SHORT transcripts and a negation
    token ("nahi karo") overrides an affirmative verb ("karo"). (d) Wake
    word runs the custom "jarvis" model ALONGSIDE pretrained "hey_jarvis"
    (openWakeWord scores a model list; `detected()` takes the max — no
    scoring change needed); the custom `models/jarvis.onnx` is the owner's
    Colab step and everything ships demoable on the pretrained model.
89. **Memory v2 — budget trim at the dispatch seam (P4)**: per-brain history
    budgets are enforced by ONE pure `core/context.py::trim(messages, budget)`
    called at each concrete provider dispatch — in both routers, including the
    mid-loop fallback and privacy-pin handoffs (re-trim the same array to the
    new brain's `max_history_tokens`; the rolling summary substitutes for
    dropped turns). Trim pins every `system` message (head prompt, rolling
    summary, RAG block, trailing nudge) and tool schemas; it drops whole turns
    oldest-first, never splits a `tool_call`/`tool_result` pair, and always
    keeps the newest turn. This is the ONE owner-authorized seam in the
    otherwise-frozen v1.1.0 router — states, ladder and rate bucket are
    untouched; the only new call is `trim()` where a provider is invoked, and
    it is a no-op under `memory.engine: v1` or an unset budget. We do NOT rely
    on Ollama `num_ctx` truncation (silent, front-drops the pinned summary).
90. **Memory v2 — cross-session RAG + true-amnesia wipe (P4)**. (a) Past
    messages are embedded into a `message_vectors` vec0 table that mirrors
    `fact_vectors`; each turn injects a dated "Relevant past context" block
    (top-k=4, `min_similarity` floor) via `store.search_messages`, excluding
    the current conversation's in-window rows. Embedding is **live** (post-turn
    maintenance, off the reply path) PLUS a nightly reconciler + one-time
    backfill — so same-session and prior-session recall both work. All of this
    rides `memory.engine: v2` (`rag_k` = 0 under v1 disables injection AND live
    embed for a one-line rollback). (b) Clear/forget/wipe are deterministic
    commands intercepted at the TOP of `AgentCore.run_turn` (one impl reaches
    cli/ui/voice/telegram, model-free): "new chat"/"clear" rotate the
    conversation, "forget that" deactivates the newest fact, "wipe all memory"
    ARMS a one-shot `pending_wipe` challenge that only an explicit "confirm
    wipe"/"haan sab mitao" completes — a stray "yes" never erases. (c) Wipe =
    true amnesia: `store.wipe_all()` deletes facts + all vectors + **raw
    messages** + summaries and resets every watermark, then VACUUMs; two guards
    keep it from un-wiping — dropping raw turns + resetting the embed watermark
    means the nightly reconciler finds nothing pre-wipe to re-embed, and the
    handler flushes the live session to a fresh conversation id so Baby stops
    "remembering" immediately, not after a restart. `audit_log` is retained
    (the wipe is audited); the two-step challenge (voice/text) or a typed
    "WIPE" (UI modal) is its safety — never a single action.
91. **Token telemetry — usage_log at turn grain, capture at the shared seam
    (P5)**. Every OpenAI-wire response already carries a `usage` object; we
    stopped throwing it away. (a) Providers request
    `stream_options={"include_usage": true}` (NIM/OpenRouter, Gemini, Ollama),
    guarded by `telemetry.emit_usage` (default true) so a host that 4xxs on the
    param can be turned off per deploy without a code change. (b) The shared
    `accumulate_stream` reads usage off ANY event and captures it into
    `Chunk.usage`. The include_usage trailer arrives AFTER `finish_reason` (an
    event with empty `choices`), but the terminating `done` chunk (which carries
    tool_calls and ends the turn) is still yielded AT `finish_reason` — the
    trailer is then read best-effort with a bounded wait (`_TRAILER_TIMEOUT_S`)
    that swallows any stall or drop. This matters because the router treats a
    long post-content stall as a mid-reply abort with no failover: draining
    unboundedly toward the trailer would let a slow/dropped trailer fail an
    already-complete reply or lose a tool round (caught in P5 adversarial
    review). Capture is null-safe, so a host that omits usage degrades to blank
    counts, never a crash. (c) A turn spends
    tokens across several generations (tool-loop rounds + the empty-reply
    finalizer + the next-step offer); the agent SUMS them into `self._turn_tokens`
    and writes ONE `usage_log` row per turn. We chose a dedicated `usage_log`
    table (keyed to the P2 `turn_id`) over new `audit_log` columns because audit
    is tool-grain (one row per tool call) — the wrong grain for per-turn totals;
    the table auto-creates via `CREATE TABLE IF NOT EXISTS` on connect, so no
    migration-script row is needed. (d) Ollama reports native eval counts through
    the same wire fields; those turns are recorded and labeled "local — no quota"
    in the UI since they cost nothing. This stays within the frozen-router rule:
    the only provider-layer change is asking for a field the API already sends
    plus reading it — no router state/ladder/bucket touched.

## v3 — The Brain (2026-07-08)

92. **Ground-truth check vs the v3 spec's §0 (B0)**. The spec's stated "ground
    truth" was verified against the repo; brain lineup matched (OpenRouter
    `openai/gpt-4o-mini` primary, NIM `z-ai/glm-5.2` heavy, Gemini
    `gemini-flash-latest` backstop, local `qwen3.5:9b`), but three claims were
    stale and shape later phases: (a) the voice pipeline has only
    `IDLE/LISTENING/RESPONDING` (`voice/pipeline.py:38`) — the spec's
    `thinking/speaking/executing` do NOT exist, so B1's `/ws/state` must
    SYNTHESIZE them from bus events, not read them off the pipeline; (b) there is
    no FTS anywhere and `audit_log` is write-only, so the B5 omnibox is greenfield
    FTS5 + new read methods; (c) `models/jarvis.onnx` is not on disk — the custom
    wake word is still deferred (owner Colab step), the loader already handles a
    model list via `max()`. Also: tool `schemas()` returns all tools unfiltered
    (`tool_flags` is greenfield for B4), and task-cancel exists in code but is
    unexposed while scheduler run-now does not exist (both new routes in B4).
93. **Dual-UI serving, build-on-setup, dist NOT committed (B0)**. `create_app`
    reads `ui.frontend` (`v3|classic`, default `classic`): `v3` serves the built
    `ui/app/dist/index.html` at `/` with Vite's `/assets/*` mounted; the vanilla
    `ui/web` shell is always mounted at `/classic` so the config-first rollback
    holds for the whole branch. If `ui.frontend: v3` but `dist/` isn't built, `/`
    falls back to classic with a logged warning (graceful, never a crash). The
    built `dist/` is gitignored and produced by `scripts/setup.ps1`
    (`npm ci && npm run build`) rather than committed — build artifacts don't
    belong in git, and production serving needs no Node (static files only).
94. **.gitignore anchoring for the Node subproject (B0)**. The existing
    "graphify wrapper junk" rules (`node_modules/`, `package.json`,
    `package-lock.json`) were unanchored and would have swallowed
    `ui/app/package.json` + its lockfile (which MUST be committed for a
    reproducible pinned build). Root-anchored them (`/node_modules/`,
    `/package.json`, `/package-lock.json`) and added explicit
    `ui/app/node_modules/` + `ui/app/dist/` ignores.
95. **Frontend stack pins (B0)**. React 18.3.1, react-dom 18.3.1, zustand 5.0.2,
    react-force-graph-2d 1.27.1 (canvas-2D — the GPU belongs to the LLM);
    build tooling Vite 6.4.3, @vitejs/plugin-react 4.3.4, TypeScript 5.6.3. Vite
    was bumped from 6.0.7 → 6.4.3 to clear the esbuild dev-server advisory
    (GHSA-67mh-4wv8-2f99); it is dev-server-only and does not touch the static
    production build, but the tree is kept at 0 audit vulnerabilities.
    `package-lock.json` is committed as the true pin.
