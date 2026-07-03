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
