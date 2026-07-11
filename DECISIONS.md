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
96. **Auto-derived graph topology (B1a)**. `core/nodes.py::build_graph(config)`
    returns `{nodes, edges}` for the v3 graph. Tool nodes are derived live from
    `registry.schemas()` (add a `@tool` → a node appears, no edit) and brain nodes
    from the config `models` block + router role map, so both track reality without
    hand-maintenance. Only the fixed subsystems and the static call-path edges
    (every brain routes through `safety_gate` before any `tool:*`) are declared by
    hand — they are stable architecture. A tool's "safety class" on its node is a
    heuristic (from the `classify_tool` branches) since the real class is decided
    at call time from the args, not stored.
97. **Additive event attribution (B1b)**. `source`/`target` (node ids) and
    `turn_id` were added to bus events by passing extra kwargs to the existing
    `publish(kind, channel, **payload)` — zero dataclass/signature change, and the
    fields auto-appear in the ws JSON. `turn_id` was threaded into `_execute_tool`
    (it wasn't in scope there). `/classic` ignores the unknown keys, so it is
    unaffected. Token `source` falls back to `brain:daily` when the provider
    reports no routing decision (a plain provider in tests). The router's decision
    status stays quiet on the happy path (primary route) as before — only its
    payload gained source/target, no new emissions.
98. **Synthesized `/ws/state` (B1c)**. The gauge states `thinking / speaking /
    executing` do NOT exist in the voice pipeline (only IDLE/LISTENING/RESPONDING).
    A `_StateDeriver` folds the bus stream into them: turn_start→thinking, first
    token→speaking, tool_start→executing (until all open tools close, tracked by
    call_id), turn_end→idle; the voice "listening" status maps to listening.
    `/ws/state` sends `{state, router, game_mode}` on change (router health +
    game mode read live off the provider, since they change without a state
    transition — e.g. Wi-Fi drop → cloud→degraded). This is the spec's biggest
    factual gap vs the repo.
99. **Per-node stats + additive `audit_log.duration_ms` (B1d)**. Tool latency
    percentiles need a duration, which `audit_log` never stored — added a nullable
    `duration_ms` column (idempotent ALTER) and time ONLY the `registry.dispatch`
    call in `_execute_tool` (never the confirm wait, which is user think-time and
    would poison latency). Old rows are NULL → excluded from percentiles. Brain
    latency reuses the router's in-memory first-token samples (no new storage).
    `ctx.scheduler` was also attached to `UIContext` (it was created but never
    exposed) so scheduler/task-queue nodes can report jobs + depth.
100. **External-content FTS5 + status join for search (B1e)**. Search is greenfield
    FTS5 (compiled into sqlite here, not a loadable extension). The mirrors
    (`messages_fts`/`tasks_fts`/`audit_fts`) use `content=` external-content tables
    kept in sync by INSERT/UPDATE/DELETE triggers — no duplicated text. The
    messages query joins back to `messages WHERE status='ok'`, so quarantining a
    turn (a status UPDATE, content unchanged) drops it from search with no FTS
    re-sync. `GET /api/search` fans out grouped by type (facts + conversations via
    the vector store, activity + tasks via FTS) — cosine and bm25 ranks aren't
    comparable, so there is no cross-type global ordering. The MATCH string is
    built by quoting `\W`-split tokens (last one prefixed `*`), so arbitrary user
    text and FTS operators can never raise a syntax error. `scripts/backfill_fts.py`
    rebuilds the indexes for pre-B1 rows.

101. **v3 chat = sanitized-markdown, final reply only (B2)**. The React chat
    renders the authoritative `turn_end.reply` as markdown; streaming tokens stay
    plain text (React `textContent`), so a partial stream can never inject markup and
    there is no partial-markdown flicker. Defense in depth: `marked` with raw HTML
    disabled (layer 1) → `DOMPurify` allowlist sanitize (layer 2) → a DOMPurify hook
    stamps `rel="noopener noreferrer" target="_blank"` on every link. Frontend tests
    are light `vitest` (jsdom) on pure logic only (store reducers, layout math,
    markdown, and in B3 the edge-derivation map) — no DOM/component tests.

102. **Honest signal→edge derivation for pulses (B3)**. Graph particles animate only
    real edges, derived from the current turn's own signals — never faked, never
    cross-turn. Attributed directly: `tool_start`/`tool_end` (`brain:{tier}` →
    `safety_gate` → `tool:{name}`, colored by `safety_class`; error status flashes the
    tool node) and `CloudRouter` router `status` (`router → brain:{tier}`). Derived:
    `turn_start` → `baby_core → router`; `turn_end.brain` (the authoritative authoring
    brain) → `router → brain:{tier}` (covers the silent default `nim_primary` route,
    which emits no decision event); voice `status "heard …"` → `voice_stt → router`;
    voice `token` → `baby_core → voice_tts`. **`backstop → cloud` remap** (router tier
    token `backstop` vs graph node `brain:cloud`, the config key). Left DARK — no
    honest signal, so never pulsed: `brain → mem_*` (memory access never hits the bus)
    and per-stage voice `voice_wake`/`voice_vad` (only aggregate `voice:` text). The
    full map lives in `ui/app/src/graph/edgeMap.ts` and is unit-tested. Text-turn
    replies have no return edge in the topology, so they are shown by the core gauge's
    "speaking" state rather than a faked `brain → baby_core` edge.

103. **Idle-throttled render clock; gauge breathes on the canvas (B3)**. The central
    Baby-core node is the status gauge and breathes on the canvas, but a naive
    `autoPauseRedraw=false` would repaint at 60fps forever (the GPU belongs to the
    LLM). Instead we own the only draw loop: the force-graph engine loop stays paused
    and one self-managed rAF forces a single repaint per tick via a
    `resumeAnimation()→pauseAnimation()` one-shot (`autoPauseRedraw=false` so the frame
    always paints). Cadence: 60fps while active (a turn running or a pulse in flight),
    ~20–24fps when idle, no draws at all in low-power-idle, hard-pause when the tab is
    hidden. Breath phase comes from `performance.now()` so it looks right at any fps.
    `performance_mode` (header ⚡, persisted to localStorage) and
    `prefers-reduced-motion` drop to static state-color + no particles — but
    `performance_mode` is a **user opt-in, NOT default-on**: the perf gate must clear
    with it off (owner rider).

104. **Pulse bus outside zustand + per-edge coalescing (B3)**. Pulses fire at token
    rate; routing them through the React store would re-render per token and blow the
    perf budget. `ui/app/src/graph/pulseBus.ts` is a module-level pub/sub — hooks emit,
    `BrainGraph` subscribes and paints. `emitParticle` needs the exact link object from
    `graphData.links` (identity match, no id lookup), so BrainGraph builds a
    `from>to → link` map once per topology. Particles are coalesced per edge (≥150ms
    apart → ≤~6/s/edge), so a burst of 50 tool events shows as one visible stream.

105. **tool_flags: additive table, schema-hiding, gate untouched (B4)**. An additive
    `CREATE TABLE IF NOT EXISTS tool_flags(name, enabled, updated_at)` (auto-creates on
    connect; no `_migrate`, no backup). `registry.schemas(disabled)` hides a disabled
    tool from the model; `AgentCore` reads `db.disabled_tools()` once per turn and passes
    the set at the single `provider.chat` call. This is **structurally disjoint** from
    the safety gate (`agent.py` `gate.classify` at a separate seam) — a disabled tool is
    still classified normally, so no flag can ever weaken the gate. The flag **setter
    rejects any name not in the live registry** (`registry.is_registered`), so the gate
    (a subsystem node, not a tool) is unrepresentable as a flag. Enforced by
    `tests/test_safety.py` (the forever-green file) + `tests/test_tool_flags.py`.

106. **Best-brain boost, not a per-tier pin (B4)**. The brain "prefer this brain"
    control reuses the existing `tier_hint="best"` as a **one-shot** `AgentCore`
    field, consumed for exactly the next turn — zero router change. Verified precedence:
    `tier_hint` is #7 in `CloudRouter._ladder`, strictly below every privacy/language
    force-local pin (677/679/692) and offline/degraded (697/699), with `_redact_pinned`
    (777) as a second layer — so a boost can never send local-pinned content to the
    cloud, and a down-cloud boost still degrades to local. Honest placement: the control
    lives ONLY on the `brain:nim_heavy` drawer + a chat-input ⚡ (a "boost armed" chip
    with cancel; auto-clears on `turn_end`); other brain drawers are read-only (a
    per-tier pin doesn't exist). Arming is audited as `explicit_request`. A true
    per-tier pin is parked for a possible post-v3 PR.

107. **Additive control endpoints + inspector plumbing (B4)**. `POST /api/tools/{name}/
    flag`, `/api/brain/boost`, `/api/tasks/{id}/cancel` (`WorkerPool.cancel`),
    `/api/scheduler/{id}/run` (new `Scheduler.run_now` invoking the job's own registered
    coroutine — the exact cron path) — all non-destructive, plain POST, None-guarded.
    `/api/nodes/{id}/stats` gained read-only `enabled` (tool) / `pinned_next_turn`
    (brain). Frontend: the topology is lifted into the zustand store (`graph`) so the
    inspector drawer — mounted as an overlay sibling — can resolve `selectedNode` → node;
    `MemoryPanel` is shared by the dialog and the memory-node drawer; a `#node/<id>`
    deep-link (two-way hash sync) drives selection + camera fly-to, reused by B5 search.

108. **Omnibox reuses the `selectNode` cascade; zero backend change (B5)**. The
    "Search the brain…" omnibox is pure frontend — the search backend (grouped FTS5 +
    vector fan-out) shipped whole in B1. Selecting a result is a single
    `useBrain.selectNode(item.node_id)`, which already drives camera fly-to (`BrainGraph`),
    the `#node/<id>` hash (`useDeepLink`), and the inspector drawer — so no new focus
    plumbing. The server stamps every result's anchor `node_id` (fact→`mem_facts`,
    conversation→`mem_rag`, activity→`tool:<name>`, task→`task_queue`) and exposes no
    comparable cross-type score (cosine vs bm25), so results are grouped, never globally
    ranked: fixed group order (Facts → Conversations → Activity → Tasks), server
    intra-group order preserved. Ordering/flatten/select-map + recents live in the pure,
    unit-tested `ui/app/src/lib/searchResults.ts`.

109. **Honest select + best-effort highlight; never fabricate a target (B5, owner
    riders)**. `resultAction` reads only the server-stamped `node_id` — it never invents
    an anchor — and the omnibox verifies that node exists in the loaded graph before
    selecting, so a de-registered tool's audit row (missing `tool:<name>`) shows a toast
    instead of opening a dangling empty drawer. A **fact** result requests a best-effort
    highlight (`store.focusFact`): `MemoryPanel` rings + scrolls to that fact **only when
    it's already in the loaded browse list** — un-present facts are not faked. Old **audit**
    events get no scroll-to: the live-event ring is session-only, so there is no per-node
    history to scroll to, and B5 adds no new fetch for one (revisit only on real need).
    `focusFact` auto-clears whenever `selectedNode` moves off `mem_facts`.

110. **Conversation results fly to `mem_rag` + Chat tab, not a fake reload (B5)**. Chat
    is a single live stream — `/history` returns only the active `conversation_id` and
    the only conversation control (`POST /conversation/new`) *replaces* it; there is no
    load-arbitrary-conversation-by-id backend. So a conversation result flies the camera
    to `mem_rag` and switches the right panel to the Chat tab (showing the real snippet
    in the result), rather than pretending to reopen that past thread in the live stream.
    A true read-only conversation viewer (new additive read endpoint + overlay) is parked
    for a possible post-v3 PR. Recent searches persist in `localStorage`
    (`baby.recentSearches`); the omnibox is focus-summoned via Ctrl/⌘-K or `/`.

111. **Speaker-verify v2 stays on sherpa-onnx (no torch); TitaNet-large is the
    ECAPA-lineage bench candidate (B6)**. v1's CAM++ false-rejected natural speech —
    the hypothesis is method, not model. The bench re-tests the incumbent CAM++
    (`wespeaker_en_voxceleb_CAM++.onnx`) against ERes2Net-en
    (`3dspeaker_speech_eres2net_sv_en_voxceleb_16k.onnx`) and TitaNet-large
    (`nemo_en_titanet_large.onnx`), plus SpeakerNet
    (`nemo_en_speakerverification_speakernet.onnx`) as a near-zero-cost 4th. There is
    no ready SpeechBrain-ECAPA ONNX in the sherpa-onnx release and a real export needs
    torch/Colab (rejected), so TitaNet (NeMo, ECAPA-lineage) stands in for "an ECAPA
    export". `setup.ps1` downloads all candidates fail-soft (~143 MB, bench-only). The
    B7 FAR/FRR report (`scripts/speaker_report.py`) picks the winner → set in config.

112. **The 3-tier trust ladder collapses to the existing binary gate flag — zero
    safety-gate logic change (B6)**. The gate exposes one binary signal
    (`SafetySession.unverified_channels`: in-set → blanket DENY = chat-only; absent →
    normal). Frozen-ground forbids adding gate logic and the spec says "feed-only", so
    the voice layer computes the tier and writes the same
    `SafetyGate.set_voice_verified(channel, bool)`: **trusted** and **uncertain** →
    `True` (allow-through; CONFIRM/DENY still hit the on-screen modal, voice-yes already
    rejected — Decision #46), **unknown** → `False` (chat-only). Tier is display-only
    (surfaced on the `speaker_verify` node). Trust is a `SessionTrust` smoother
    (optimistic-demote): a fresh non-PTT session starts TRUSTED and drops to UNKNOWN
    only when the smoothed score stays ≤ reject for `demote_after` utterances — no
    single shaky utterance locks the owner out (v1's failure). PTT still auto-trusts;
    "baby stop" is checked before verification so it never depends on trust. A profile
    is now a SET of centroids (`speaker_profiles` table, one row per mic-position/
    session, `struct.pack` float32 BLOB) scored by MAX cosine — robust to distance/
    energy variance; single-mean is the 1-centroid case. Load is DB-centroids-first with
    the v1 `owner_voice.json` mean as fallback, so every existing test + v1 enrolment
    still works.

113. **`mode: observe` + additive per-utterance audit logging feed the B7 soak (B6)**.
    A third mode `observe` (alongside `chat_only`/`ignore`) scores + logs every utterance
    but never enforces — the B7 3-day soak runs mechanism-on / gate-off to collect
    `(score, tier, decision, model)` as `add_audit("speaker_verify", …)` rows (mirroring
    the router's overloaded audit pattern; v1 logged nothing). `speaker_report.py` reads
    them back per model and computes FAR/FRR from time-windowed ground truth (owner
    window vs an optional non-owner window). B6 ships `enabled: false`; B7 flips it on
    only if owner FRR ≤ 2% AND 0 non-owner accepted — shipping OFF with findings is an
    acceptable outcome. Wake-word (`models/jarvis.onnx`) remains an owner Colab item;
    `wakeword_models: []` + the list-loader already support it with no code change.

114. **B7 WS resilience is a frontend-only job — the backend is leak-free**. Verified
    every `/ws/*` handler cancels its pump in `finally` and each pump unsubscribes from
    the bus in its own `finally` (`ui/server.py`), and every in-memory collection is
    bounded (latency `del samples[:-500]`, bus queue `maxsize=512`, session-trust
    `deque(maxlen)`). So B7 adds no backend leak-fix and no router/gate/provider change.
    The gap was purely client-side: `socket.ts` surfaced no connection state, the
    `connected` store flag was write-only/never read, and on a backend drop the header
    froze while a mid-stream bubble stayed `streaming: true` forever. Fix: an additive
    `onStatus(up)` callback on `openSocket` (open→true, close/error→false; backoff/retry
    and 2-arg callers unchanged) drives per-channel `ws: {chat, activity, state}` in the
    store (replacing the dead flag); the header shows a reconnect pill while any channel
    is down ("connecting…" before first `/stats`, "reconnecting…" after); and a dropped
    chat socket calls `interruptTurn()` to finalize the open bubble (kills the stuck
    cursor) + drop a one-line note. Honest empty/down states on the chat + activity
    panels.

115. **Responsive = drawers over the graph, not stacked scroll (B7)**. At ≤720px the
    graph stays full-bleed + pannable as the centerpiece; the side panel becomes a fixed
    slide-over (with a tap-away backdrop, reusing the existing collapse toggle — default
    collapsed on a phone-width viewport), the inspector goes full-width, the omnibox
    full-width, and the header gauges hide. Stacked-scroll was rejected because it
    demotes and scrolls away the graph, which is the whole point of the UI. Pure CSS
    `@media` + one backdrop element (CSS-gated to mobile); no new layout state.

116. **Long-session hygiene: cap the two remaining unbounded arrays; gate the cursor
    keyframe (B7)**. The event ring was already capped (500); B7 caps the chat transcript
    (`MESSAGE_CAP = 300`, front-trim so a still-streaming tail is never dropped) and the
    toast stack (5). Backend collections are all bounded (see #114), so "heap stable over
    hours" is a frontend concern only. Separately, `prefers-reduced-motion` zeroes CSS
    `transition`s via the `--dur-*` tokens but **not** `@keyframes`, so the streaming
    `blink` cursor kept animating — now gated under a reduced-motion media query.

117. **Release sequencing: code-complete commit, PR stays Draft; the soak/flip is
    owner-driven (B7)**. The PR §12 checklist depends on data that does not exist until
    the owner runs the 3-day soak — the perf-gate numbers (which must clear with
    `performance_mode` **OFF** — it is a user opt-in, never a default-on ship for the
    centerpiece) and the speaker FAR/FRR report. So the executor ships the B7 polish
    commit and stops with the PR in **Draft**; the owner runs the soak, decides
    `voice.speaker_verify.enabled` from the real curves (ON only if owner FRR ≤ 2% AND 0
    non-owner accepted, else OFF with findings), then flips Ready + merges + tags
    `v3.0.0`. The disruptive `e2e_regression.py --with-project` + v2/v1.1 browser/TTS
    demos are likewise owner-run (they open a real Chromium window and may speak); the
    executor runs only the always-green gates (`pytest` + `ruff` + FE `build`/`test`).

118. **v4 deliberately reverses v3's "canvas-2D only, the GPU belongs to the LLM"
    law (V0).** v3 banned WebGL/3D because the local 9B was primary and the GPU ran
    hot every turn. Since the NIM/OpenRouter migration cloud is primary and local is
    fallback, so the GPU sits idle on most turns — precisely when a 3D brain renders
    for free. The one collision (local model generating while peak spectacle is
    wanted) is arbitrated by v4's frame governor + VRAM watchdog (V2), not banned.
    Recorded here as a deliberate architectural decision, not drift. v3's other law —
    honest data only — survives untouched: every directed animation rides a real
    signal; ambient idle rotation is the sole non-signal allowance.

119. **The v4 native shell is thin chrome over the FastAPI-served frontend, not a
    second copy of the SPA (V0).** The shell window loads `http://127.0.0.1:8765/`
    (the same `ui/app/dist` FastAPI already serves) — it does not bundle its own
    frontend. Consequences: one build (`ui.brain: 3d|2d`, the 3D sphere, and the
    motion system all live in `ui/app/` and render identically in browser and shell,
    no drift); `ui.shell: browser` is non-bricking by construction (it just means
    "don't launch the exe" — the backend and browser UI are untouched); the shell's
    only jobs are native window, tray, single-instance, close-to-tray,
    attach-or-spawn, installer, autostart — zero product logic.

120. **v4 "Quit Baby (app)" closes the window; the always-on service persists — no
    HTTP shutdown endpoint (V0).** The native tray item is labelled **"Quit Baby
    (app)"**: it closes the shell window and kills only a backend the shell itself
    spawned; an attached always-on autostart service (Telegram, scheduler, background
    tasks) keeps running. Stopping the service is a separate, clearly-labelled
    documented action outside the app (`autostart.ps1 -Remove` / a `Stop Baby
    service` snippet), never an app menu item and never a remote endpoint. This keeps
    the branch inside the spec §0.4 additive surface (VRAM + mic/TTS amplitude on the
    event stream, `ui.shell`/`ui.brain` config reads) — no `POST /shutdown`, so
    frozen ground (router/provider/safety) is untouched.

121. **Shell = Tauri, not Electron; measured on the 5060 Ti (V0c).** The V0 spike
    built byte-identical bloom-sphere scenes (~42 nodes + ~50 arcs + EffectComposer
    bloom, shared `spike/common/`, fixed 60 s camera path) in both shells and
    self-measured. Results (owner box, 144 Hz panel, uncapped rAF so p50 tracks
    vsync — the discriminators are 1%-low, cold-start, footprint, and the
    owner-judged bloom/VRAM):

    | metric | Electron | Tauri |
    |---|---|---|
    | fps p50 | 144.9 | 142.9 (tie — vsync) |
    | fps 1%-low | 95.4 | 49.8 |
    | cold-start shell (ms) | 469 | 280 |
    | installer | ~85 MB (bundled Chromium) | **1.7 MB** (NSIS) |
    | unpacked | 269 MB | **5.9 MB** (shared WebView2) |
    | VRAM delta | owner-deferred | owner-deferred |
    | bloom acceptable | owner-deferred | owner-deferred |

    **Chosen: Tauri.** (1) Footprint — 5.9 MB exe / 1.7 MB installer vs Electron's
    269 MB / ~85 MB, a ~45x win, decisive for an app the owner installs + updates.
    (2) Idle GPU/RAM — Electron bundles its own Chromium (separate GPU process +
    caches); Tauri shares the OS WebView2, so its resident footprint is lighter,
    which directly serves the v4 core law (#118): the shell must leave VRAM free for
    the local 9B in the collision window. (3) Cold-start 280 vs 469 ms. (4) Bloom
    risk is low — WebView2 is Edge/Chromium, the same WebGL2 + float-framebuffer path
    Electron uses. (5) Spec §3 leaned Tauri. **Accepted risk:** Tauri's 1%-low
    (49.8) dips under 60 in a *no-load* spike vs Electron's 95.4; the V2 frame
    governor + fixed-timestep + 60-cap are built to absorb exactly this, and the
    thin-shell architecture (#119) makes a shell swap cheap (the frontend is
    untouched) if WebView2 bloom disappoints once the real sphere lands in V3.
    **Owner-deferred, non-blocking:** the real VRAM delta (needs the backend up) and
    the bloom eyeball were not captured before the decision; both are re-checked
    against the real 3D sphere in V3, where `ui.shell`/`ui.brain` rollback plus the
    cheap shell-swap keep the choice reversible. The `spike/` folder is deleted; the
    winner is scaffolded at `ui/shell/`.

122. **v4 native shell parity — how it attaches, trays, and quits (V1).** The shell
    is pure native chrome; V1 wires the Docker-Desktop lifecycle with zero product
    logic and exactly one additive backend read.
    - **Readiness = a reachable TCP :8765**, not an HTTP health check. uvicorn binds
      only after `ready_check` loads the model (ui/server.py), so a connectable port
      already means "model up" — the shell needs no `/stats` parsing and no health
      endpoint (there is none; frozen ground stays intact).
    - **Attach-or-spawn.** The shell probes :8765; if up it ATTACHES; else it SPAWNS
      `pythonw run.py --all` (detached, no console) from `BABY_HOME` — the env var, or
      the first ancestor of the exe that contains `run.py` + `.venv` (true in dev). An
      installed shell with no repo shows a "start the backend" splash instead of
      guessing. It records whether it was the spawner. In autostart native mode the "Baby Shell" task launches with `--attach-only`, so the shell WAITS for the always-on service to bind and never spawns a duplicate — the two logon tasks cannot race two backends and the service always persists.
    - **Quit kills only what it spawned (#120).** "Quit Baby (app)" closes the window
      and kills a shell-spawned backend; an attached always-on service is left
      running. The window X hides to tray (close-to-tray); only the tray item quits.
      No `POST /shutdown`, no remote HTTP shutdown of any kind.
    - **Tray reconciliation.** When `ui.shell: native` the backend skips its pystray
      tray (additive `_shell_owns_tray` read at ui/server.py) and the shell owns the
      native tray. The shell folds the tray colour off **/ws/activity** (the socket
      carrying confirm + task/tool/project, which /ws/state lacks): a pending
      confirmation → red, any running tool/task/project → amber, else green. That
      stream has no `turn_start`, so a no-tool chat reply does not flash amber — a
      deliberate, negligible fidelity trade for a single-socket tray; the
      safety-critical signal (a pending confirmation) surfaces as soon as it fires; a socket that connects mid-confirmation only catches up on the next event (/ws/activity does not replay state), and a reconnect re-syncs.
    - **Autostart is two independent tasks in native mode.** `autostart.ps1` always
      registers the always-on backend service ("Baby Assistant", `pythonw`); with
      `-Shell native` it ALSO registers "Baby Shell" to open the window at logon
      (which attaches). Keeping them separate is what makes "close the app ≠ stop the
      service" true. `-Remove` drops both; stopping the service stays an explicit,
      documented action, never an app menu item or an HTTP endpoint.

123. **v4 frame governor: the 60 fps safety net, built before the 3D cliff (V2).**
    Three pure, unit-tested TS modules under `ui/app/src/graph/governor/` form the
    spine the V3 sphere + V4 motion ride on, and are testable now against the 2D
    graph:
    - **fixedTimestep** — a fixed-timestep accumulator: a frame advances real time,
      runs as many FIXED sim steps as fit (clamped, so a backgrounded tab never
      spirals trying to catch up), and exposes an interpolation alpha. This is the
      30-vs-60 fps motion identity — the same sim rate regardless of paint rate.
    - **tierMachine** — full3d → lite3d → 2d with hysteresis mirroring the router's
      demote-fast / recover-slow shape: a short sustained pressure demotes (protect
      the frame budget), promotion needs a long calm (no flapping). A lowered config
      ceiling snaps down immediately; 2d is the floor.
    - **vramWatchdog** — the collision-window enforcer of law #118: reads the VRAM
      signal and fires when the local model is resident (used ≥ 80% of total, or free
      ≤ 1.5 GB). Fail-open — no NVML → no pressure → the full experience.
    `useGovernor` wires them to one rAF loop (frame pressure OR VRAM pressure → step
    the tier machine → publish the tier), honoring the ⚡ performance opt-in as a
    ceiling cap; a spurious multi-hundred-ms stall (tab resume) is ignored so it
    cannot demote on a single spike. The ONLY backend touch is additive and squarely
    inside spec §0.4: VRAM is pushed on **/ws/state**, QUANTIZED to 0.25 GB buckets so
    the pump's exact-equality diff stays quiet at idle and fires only when usage
    crosses a bucket (the 9B loading), plus a periodic tick on the pump so an
    idle-time VRAM change still reaches the client. Additive `render.{target_fps,
    tier, idle_full_on_desktop}` config, code-defaulted, exposed on /stats. A small
    header tier chip surfaces a demote so it is observable in V2 — the 2D graph looks
    the same at any tier, so the visible payoff is V3; V2 is the safety net that lands
    first, on purpose.

124. **v4 3D neural sphere: the same honest brain, in three dimensions (V3).** Behind
    `ui.brain: 3d` (code-default `3d`; `2d` is the one-line rollback to the v3 canvas
    graph), a react-three-fiber sphere renders the identical honest-data layer the 2D
    `BrainGraph` reads — `pulseBus`, `edgeMap`, `/api/graph`, the store — so browser and
    native shell show the same truth. Only the renderer differs.
    - **Geometry.** Nodes land on deterministic sphere anchors by group region
      (Fibonacci cap patches, `graph/sphere/sphereGeometry.ts`); edges are great-circle
      arcs that bow outward, with origin edges drawn radial (`greatCircle.ts`).
      Zero-signal edges stay dark — the honest-data law, unchanged from v3.
    - **Firing = the real turn path.** `Pulses.tsx` subscribes the SAME `pulseBus`
      feed the 2D graph does; a turn sends a sprite node→node along the visible arc, a
      tool fires the real 2-hop through `safety_gate`, errors/confirms flash. No timer
      fabricates motion; every sprite rides a real `PulseAction`.
    - **Senses + mood.** `CoreGauge.tsx` is the central state gauge (idle breathe /
      listening ripple / thinking orbit / speaking shimmer / executing sweep). The
      ripple and shimmer ride REAL loudness — the ONLY backend touch this whole phase,
      and strictly additive per spec §0.4: `mic_rms` (RMS-computed, quantized, and
      throttled to ~15 Hz at the publish site, off the voice pipeline's existing
      thread-safe hand-off) and `tts_rms` (RMS-computed per 100 ms audio chunk in
      `audio_io.play` via a new optional `on_level` callback — ~10 Hz by the chunk
      cadence, no extra throttle — then quantized at the publish site). Both are added
      to `_ACTIVITY_KINDS`; router / provider / safety logic and `tests/test_safety.py`
      are untouched.
    - **Amplitude is a module singleton, not the store.** A 15 Hz `set()` would
      re-render every subscriber, so `graph/amplitude.ts` holds the levels in a module
      mutable (the `pulseBus` precedent); `foldAmplitude` intercepts `mic_rms`/`tts_rms`
      in the activity socket BEFORE `pushEvent`, else the 500-cap event ring floods in
      ~33 s. Node recolor rides router health (offline→red / degraded→amber on the
      cloud brains), `activeBrain` highlights the answering brain, and game-mode ghosts
      the local 9B.
    - **The governor is the sole on/off seam.** `ui.brain` + `render.tier` fold into
      one `renderCeiling`; the V2 tier machine's `renderTier` gates what draws
      (`tierToRender`) — bloom + particles shed first on demote, and the 2D graph is the
      floor. No new flag surface.
    - **Context-loss floor + bounded backoff.** A dead WebGL context (often the local
      9B holding VRAM for a whole offline turn) calls `preventDefault()` and falls to
      the 2D graph via a `contextLost` flag + an `App.tsx` `SphereBoundary` error
      boundary — never a black stage, never a hot lost-context loop. The retry backs
      off on losses that keep recurring (60 s → 2 m → 5 m cap,
      `graph/sphere/contextLossBackoff.ts`) to quiesce a dead GPU, and resets to the 60 s
      fuse when a loss arrives only after a long clean gap. The recovery signal is the
      **inter-loss gap**, not "did the remount survive a few seconds" — a flaky GPU whose
      fresh context dies after ~10 s survives any short grace yet is still dead, so a
      grace-based reset never climbs for it (adversarial review caught this and drove the
      rewrite). A full remount IS the recovery path, so there is no
      `webglcontextrestored` handler (it would race the fresh canvas).
    - **`@react-three/drei` dropped** — `OrbitControls` comes straight from
      `three/examples/jsm/controls` (saves ~150 KB + one pin). Deps pinned:
      `three@0.169.0`, `@react-three/fiber@8.17.10`,
      `@react-three/postprocessing@2.16.3`, `postprocessing@6.36.4`. The sphere is
      lazy-loaded, so `ui.brain: 2d` / weak machines never fetch `three`.

125. **v4 motion system: CSS-first, superseding the spec's framer-motion + lucide lock
    (V4).** Spec §3 locked "Motion (framer-motion) + lucide" for the animated UI. Deferred
    to build time, that lock lost to the repo's own shape, so v4 **reverses it** (recorded
    here as a decision, not drift; owner-approved): the UI is 100 % global CSS + CSS-var
    tokens (`styles/tokens.css` + `styles/app.css`) with motion tokens already present;
    modals are **native `<dialog>`** (`showModal()/close()`), which framer's
    `AnimatePresence` exit-animation fights; tests are **pure-logic only, no component/DOM
    tests by design**, so framer JSX would be untestable in the repo norm; and a heavy
    dep cuts against the thin-shell footprint (#119). So the motion system is plain CSS
    driving off shared tokens — **zero new deps**.
    - **One collapse lever, three triggers.** A pure `graph/motion.ts`
      `motionLevel(reduced, performanceMode, tier) → full | lite | off` folds OS
      reduced-motion, the performanceMode opt-in, and the governor's 2D floor into one
      verdict; `useMotionFlag` publishes it to `<body data-motion>`. All enter/hover
      motion uses the `--dur-*` durations, and reduced-motion / `data-motion="off"` both
      zero those durations — so decorative motion collapses through the SAME mechanism
      that already served prefers-reduced-motion. Continuous emoji loops (literal
      durations) are gated off explicitly for reduced / off / lite.
    - **#116 consolidated.** The three ad-hoc `matchMedia("(prefers-reduced-motion)")`
      reads (Scene / CoreGauge / BrainGraph) collapse into one live `useReducedMotion`
      hook — reduced-motion is now honored app-wide from a single source.
    - **Cohesion via palette, not a new icon set.** `<body data-pstate>` drives
      `--accent-live` so chrome accents (active-tab underline, omnibox focus ring) track
      the live pipeline hue, matching the sphere gauge (`--accent` already equals the
      sphere's `--state-thinking` blue). **Emoji icons stay** — a lucide swap is a
      deliberate rebrand, parked, not a v4 polish item.
    - **Safety.** The confirmation `<dialog>` gets ENTER animation only; Approve/Deny/Esc
      keep firing `postConfirm` synchronously and the dialog closes instantly — **no user
      action is ever gated behind an animation.** Pure frontend; no backend touch, so
      frozen ground and `tests/test_safety.py` are untouched, and v4 adds **no config
      flag** (reduced-motion is OS/CSS, performanceMode is the existing localStorage
      opt-in).

126. **v5 chat history & default cloud mode — surface what exists, purge what deletes,
    boot lighter (H0–H4).** v5 is mostly *surfacing*: the `conversations` + `messages`
    tables, rolling summaries, and the FTS/vector search all predate it, so the branch
    adds only additive read endpoints + UI plus one scoped delete and one boot flag.
    Frozen ground holds — no router/provider/safety logic changed.
    - **Derived metadata, not new storage.** `conversations` gained only `title` +
      `archived` (additive `_migrate`); `message_count`, `last_message_at`, and the
      fallback title (explicit → summary first line → first user message → "New chat")
      are DERIVED in `list_conversations` / `get_conversation_meta`. The list is scoped
      `channel='ui'` with `HAVING message_count > 0` so boot/`new`-created empties and
      voice/telegram/scheduler threads never pollute the UI sidebar. `active_conversation_id`
      rides the list response because the live id was otherwise only on the `turn_start`
      WS payload — the sidebar needs it to highlight "current" on a cold page load.
    - **View-only + explicit Resume, against the single stream (owner-chosen).** The
      agent is stateless between turns — `_loop` rebuilds context from
      `self.conversation_id` every turn — so **resume is just reassigning that attr**
      (`POST /api/conversations/{id}/resume`, guarded on `turn_running()`; rehydration
      honors the per-brain budget for free). But because it silently redirects the live
      stream, opening a chat is **read-only by default**; an explicit "Resume here"
      makes the redirect intentional. A store viewing-gate no-ops the streaming reducers
      while a past chat is displayed, so a live turn can't corrupt the frozen transcript.
      Switching replaces the transcript via `setTranscript` (never `appendToken`) → **no
      phantom brain pulses** (honest-data intact). The omnibox conversation-hit now
      deep-links into this viewer, closing the v3 "nowhere to land" parking-lot item.
    - **Scoped delete purges the RAG vectors explicitly (the rowid-reuse fix).**
      `MemoryStore.delete_conversation` deletes the message rows (the `messages_ad`
      trigger self-purges the FTS mirror), the `message_vectors` rows **explicitly**
      (vec0 is NOT trigger-backed — and `messages.id` is `INTEGER PRIMARY KEY` **without
      `AUTOINCREMENT`**, so a reused rowid could otherwise inherit a stale embedding),
      and `usage_log`; `audit_log` is retained (wipe_all policy). A `Database`
      base-table twin covers the memory-off path (no vectors exist). Proven
      un-resurrectable via `/api/search` on both the FTS and vector paths. Deleting the
      **live** conversation rolls the agent to a fresh one so it never points at a
      deleted row (409 only while that live turn is running). **Pin-to-top was parked**
      (optional-if-cheap, unneeded with resume + a clean list).
    - **Default cloud mode = a boot data-set, not a logic change.** `startup.cloud_mode`
      (code-default `true`) makes `ready_check` skip the VRAM-loading warm ping and
      never `sys.exit` on an unreachable Ollama (a non-fatal liveness probe feeds an
      honest note); `run_ui` then sets `provider.game_mode = True` **directly** —
      **not** `set_game_mode(True)`, which would `unload()` a never-warmed model +
      publish a spurious status. The `_ladder` already branches on `game_mode`, so no
      `core/router.py` line changes. **Privacy pins are safe by construction:** the pin
      branch sits ABOVE the game-mode branch, so a pinned `read_file`/`run_shell` turn
      still forces local (lazy Ollama load, `keep_alive:0` evict) — pinned bytes never
      reach cloud even under default cloud mode. Offline / no-key at boot: Baby still
      starts and a normal turn returns the router's existing honest "all cloud
      unreachable" message (auto-loading local for offline *normal* turns would require
      editing the frozen ladder — off the table); the mitigation is **docs + rollback**
      (`startup.cloud_mode: false` or a cloud key; the v6 installer wizard handles key
      entry for end users). Rollbacks: `ui.history: off`, `startup.cloud_mode: false` —
      both code-defaulted, never written to `config.yaml`.

## v6 — Public Windows Installer (2026-07-11)

127. **v6 W0 packaging spike — the crux is shipping Python, not the NSIS wizard.**
     v6 turns Baby from a one-user dev checkout into a downloadable Windows app.
     Before any installer code, four unknowns were spiked (throwaway `spike/`,
     deleted in W1); every dev-box-provable piece is green, and the two clean-VM
     proofs are flagged owner-run.
     - **Backend delivery = bundled `uv` + first-run `uv sync` (owner-chosen).** The
       `.exe` bundles a tiny `uv.exe` + pinned `pyproject.toml` + `uv.lock`; first-run
       does `uv python install` (managed CPython — no system Python) then `uv sync`
       into `%LOCALAPPDATA%\baby\.venv`, reusing the exact `.venv` contract the shell
       already spawns (`resolve_baby_home` is already `BABY_HOME`-aware). Freeze
       (PyInstaller) was off the table: the tree pulls `torch` (via
       `sentence-transformers` + `silero-vad`) plus ctranslate2/onnxruntime/sherpa/
       sqlite-vec/kokoro/playwright native wheels — freezing that is fragile; the venv
       is ~1–1.5 GB of prebuilt wheels instead.
     - **A green `uv sync` is not proof — a post-sync FUNCTIONAL probe is.** A native
       wheel can install yet fail to load (missing VC++ redist, bad ABI). The spike's
       `health_probe.py` imports each wheel and does a real op (loads the .pyd);
       proven all-green on the dev box incl. a live Chromium launch, and it ports
       verbatim into W3's health check. Failure UX was proven against **real** `uv`
       stderr and caught a real classifier bug — a DNS failure was mis-labeled a proxy
       problem because uv's `client error (Connect)` chain matched a naive `CONNECT`
       regex; tightened so a no-internet error never sends a user to configure a proxy.
     - **Offline-first-install is OUT of scope.** The web-installer requires first-run
       network by design (`uv sync` pulls wheels from PyPI; models from the Ollama
       registry). This keeps the release tiny and installs current-by-default.
     - **Model pull = Ollama `/api/pull` stream, resumable by construction.** Real
       byte/%/speed/ETA rendering proven; a re-issued pull completes from cached
       content-addressed blobs (the resume mechanism). The mid-pull NIC-kill is the
       one owner clean-VM check.
     - **Engine = NSIS, unchanged.** `tauri-cli 2.1.0` + the existing
       `bundle.targets:["nsis"]` already emit a ~1.6 MB unsigned `*_x64-setup.exe` —
       toolchain works, tiny size confirms the web-installer premise. NSIS covers the
       wizard needs (license/EULA page, per-user `installMode`, post-install hooks,
       native uninstaller). **Finding:** NSIS has no MSI-style Repair/Modify ARP
       dialog, so v6 does **repair + mode-switch (cloud-only ↔ full) in-app** (W5),
       leaving clean uninstall to NSIS — no WiX/MSI detour.
     - **Signing = unsigned now + hook wired.** Owner publishes free, so the build
       ships unsigned + a SmartScreen "More info → Run anyway" walkthrough + `.exe`
       checksums. The hook is a single-key drop-in later (`bundle.windows.signCommand`
       or `certificateThumbprint`+`timestampUrl`). The only free *trusted* path for a
       public OSS repo is **SignPath.io Foundation (free OSS signing)** — a parallel
       owner enrollment track, not a blocker.
     - **Boundary finalized.** `.exe` carries only shell + wizard + `uv.exe` +
       `pyproject`/`uv.lock` + `config.default.yaml` + `EULA.txt`; first-run fetches
       the managed Python, the deps, [Full] Ollama + 9B, and the voice/embedder
       assets. STOP for owner ratification before W1.
