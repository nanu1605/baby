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
