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
