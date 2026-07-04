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
