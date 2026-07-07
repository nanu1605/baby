# V2 — Conversational & Reliable (v2.0.0)

## Context

v1.1.0 shipped (cloud-primary router, tagged, merged). Owner wrote
`C:\Users\tanis\Downloads\V2_PLAN.md`: a 6-phase spec that fixes two live bugs
and adds three features. **Bugs first** — everything later sits on a reliable
loop and a clean DB.

Owner's items → phases: sensors/"(no response)" (#6→P1), DB poison (#7→P2),
continuous conversation (#2→P3), proceed/cancel (#4→P3), bigger cross-session
memory (#3→P4), clear/wipe controls (#5→P4), token telemetry (#8→P5).
Brain-graph UI (#1) is **v3, out of scope** — do not build it.

**Owner decisions (this session):**
1. Sensors = **LibreHardwareMonitor tray + WMI** (`root\LibreHardwareMonitor`), Baby stays non-elevated.
2. Wake word = **train custom single-word "jarvis"** during P3 (Colab), run alongside pretrained `hey_jarvis`.
3. Ship timing = **early hotfix v1.1.1**: after P1+P2 pass, cherry-pick them to `hotfix/v1.1.1` and ship the bug fixes; features continue on the v2 branch.

Grounding facts verified against current code (file:line) are inline per phase.

## Branch & Git strategy

- Feature branch `feature/v2-conversational-reliability` off `master`; push `-u` immediately; **draft PR** "v2: conversational & reliable" after P0, Ready for Review at end of P5.
- Branch guard every phase: `git branch --show-current` must equal the feature branch; wrong-branch uncommitted work → stash/switch/pop.
- **Zero commits to master. Tanishq merges — Claude never merges.** Tag `v2.0.0` post-merge (owner).
- **Early hotfix (decision 3):** when P1 **and** P2 are green, branch `hotfix/v1.1.1` off `master`, cherry-pick the P1+P2 fix commits, run full suite + safety gate, open its own PR for owner merge + `v1.1.1` tag. v2 branch keeps its P1/P2 commits and continues to P3.
- `tests/test_safety.py` green is the hard gate after **every** phase. Red safety suite blocks all.
- **Before any schema migration (P2, P4):** copy `baby.db` → `backups/baby-<date>.db`, verify the copy opens, before touching schema. Rollback for DB phases = restore backup + prior tag.
- Rollback is config-first: `conversation.enabled`, `memory.engine: v1|v2` flags.

## Version note

`pyproject.toml:3` is currently `version = "0.1.0"` (NOT 1.1.0 — release tags carry the real version). P0 bumps to `2.0.0-dev`; P5 finalizes `2.0.0`.

---

## P0 — Branch, scaffolding, failing repros *(½ day)*

Repro tests committed **first** — they define "fixed" and must fail for the right reasons.

- `tests/test_sensors.py`: the stats/sensors tool returns real temp data **or** structured `{"error": ...}` — never empty, never silent.
- `tests/test_db_hygiene.py`: a poisoned fixture DB (orphaned tool result; half-written turn; malformed JSON args; tool message with no preceding assistant `tool_calls`) → context builder emits an **OpenAI-valid** message sequence with poison excluded.
- `tests/test_loop_guard.py`: `FakeProvider` (tests/conftest.py:13) returns empty → loop retries once, then honest failure line; literal `"(no response)"` never reaches the user.
- Bump `pyproject.toml:3` → `2.0.0-dev`.

**Accept:** repro tests exist, run, fail for the right reasons; rest of suite green; draft PR open.

---

## P1 — Sensors + tool contract (#6) *(~2–3 days)*

Root causes (verified): `tools/system_stats.py` reads **no CPU temp at all** (psutil temps are Linux-only); `tools/registry.py:78` coerces any return to a string and `core/agent.py:272` counts anything not prefixed `{"error"` as success — so an **empty tool result passes as success**, and an empty model reply hits the `"(no response)"` fallback at `core/agent.py:239`.

**Build:**
- **Sensor source (decision 1):** LibreHardwareMonitor at login (tray, minimized, WMI publishing on); query `root\LibreHardwareMonitor` (`Hardware`+`Sensor` classes) via the `wmi` package — CPU Tctl/Tdie, per-core temps, fan RPM, voltages. GPU temp/power stays on pynvml (`system_stats.py:12`). `scripts\setup.ps1` gains an LHM install + autostart step; document the one-time admin driver approval. Record the WMI-vs-pythonnet choice in DECISIONS.md.
- **New tool `get_sensors(detail=False)`** (new `tools/sensors.py`, `@tool` per registry.py:16) — temps/fans/voltages with units; **graceful degradation**: LHM/WMI absent → `{"error": "sensor source unavailable: LibreHardwareMonitor not running", "hint": ...}`. Keep `get_system_stats` as-is for CPU%/RAM/GPU.
- **Tool-contract guard in `tools/registry.py` dispatch (line 55-81):** every tool return validated — non-empty dict or `{"error": ...}`; `None`/empty-string/`"null"`/`{}` → auto-wrapped `{"error": "tool returned no data"}`. One wrapper fixes the whole surface and closes the empty-string-is-success hole.
- **Loop guard in `core/agent.py` (around 219, 239):** empty/whitespace model output → single retry with nudge (reuse `_final_answer`, agent.py:288) → still empty → honest reply ("I hit a snag generating a response — try once more?") + audit row. Applies to every brain. Kills the `"(no response)"` literal.

**Accept:** "what's my CPU temperature?" → real Ryzen temps, visible in feed. Kill LHM → clear "what's missing" message, not silence. `test_sensors.py` + `test_loop_guard.py` green; safety green.

**→ Early-hotfix gate check after P2.**

---

## P2 — DB hygiene: never feed poison (#7) *(~3–4 days)*

Root cause (verified): messages written **per-message, non-transactionally** (`db/database.py:72` `_write` commits each call; agent adds user/tool/assistant separately); schema `db/schema.sql:10-16` = `id,conversation_id,role,content,created_at` with **no turn_id/status**; `core/agent.py:170-191` replays history with **no sanitization**. Interrupted turns leave debris that some providers reject.

**Build (backup DB first, Section 1.6):** four layers.
1. **Write clean (transactional turns).** Migration: add `turn_id` + `status` (`ok|failed|quarantined`) to `messages`. A turn's messages commit in one transaction at turn end; mid-turn crash leaves nothing or an atomically-`failed` turn.
2. **Quarantine on failure.** Any errored turn (provider rejection, tool crash, kill switch) → its rows `failed`; excluded from all future context loads (`db/database.py:136` `get_messages` gains a status filter); retained for debug + a UI "quarantined" filter.
3. **Validate on load (context sanitizer).** New strict gate before the provider call: drop orphaned tool results, tool messages lacking a preceding assistant `tool_calls`, unparseable JSON args, empty-content rows; guarantee OpenAI-valid output regardless of DB state. Each dropped row → audit `context_sanitizer`. Wire into the messages-array assembly at `core/agent.py:163-191`.
4. **Self-heal on rejection.** Provider still 4xx context error → rebuild from last-known-good turn + rolling summary (`conversations.summary`, schema.sql:5), retry once, mark offending rows `quarantined`, log loudly. User sees ≤1 short delay, never a hard loop.
- **`scripts\migrate_v2_db.py`:** backup → add columns → scan existing DB, quarantine pre-existing poison → print report.

**Accept:** `test_db_hygiene.py` green; unit tests for all four layers (poisoned fixtures, mid-turn-crash sim, self-heal path). Migration on a **copy** of real `baby.db` → report lists quarantined rows; then 20-turn conversation, zero context provider errors. Kill process mid-tool-call → restart → next turn works; broken turn shows `failed`, not in context.

**→ P1+P2 both green ⇒ cut `hotfix/v1.1.1` (cherry-pick, verify, PR for owner).**

---

## P3 — Conversation mode & proceed/cancel (#2, #4) *(~1 week)*

Grounding (verified this session, file:line): `voice/pipeline.py` states `IDLE/LISTENING/RESPONDING` (37), `run()` dispatch (309-325); `_idle` polls wake → `_enter_listening(frames, source)` (365, beep+drain+`frames.clear()`+`vad.reset()`); `_listen` VAD loop (376-456), kill-phrase after STT (408), game-command parse (418), `_submit` via `run_coroutine_threadsafe(agent.run_turn)` (455) → RESPONDING (456); `_respond` drains `sentence_q`, and the **`None` sentinel at 475-478 is where the turn ends → `state = IDLE`** (the follow-up hook). Barge-in `_barge_in_step` (506) unchanged. Config via `self.cfg.get`. Wake: `voice/wakeword.py` `Model(wakeword_models=[path])` (41) already takes a **list**, `detected()` does `max(scores.values())` (56-57). Yes/no lives only in `clients/cli.py:16` `_YES` (already multilingual) → `resolve(...)` (55). Next-step `core/agent.py::_suggest_next_step` (424-455), call site (323-331), appended as `"\n\nNext: …"`. Voice CONFIRM routes to the UI modal (`CONFIRM_SENTENCE` 35, queued 293) — NOT voice; proceed/cancel is a **separate, lighter** flow.

### P3a — `core/intents.py` + wire CONFIRM *(shared foundation)*
- New `core/intents.py`: `parse_yes(text) -> bool`, `parse_no(text) -> bool`, `is_end_phrase(text, phrases) -> bool`. EN/HI/Hinglish lexicon (seed from `cli.py:16` `_YES` + no-set: no/nahi/nahin/ruk/rehne do/skip/cancel/mat/band karo). Mirror the kill-phrase matcher: normalize (lowercase, strip punctuation) and only match **short** transcripts (≤ ~4 words) so a real question is never misread as yes/no.
- Replace `clients/cli.py:16` membership with `intents.parse_yes(answer)`.
- `tests/test_intents.py`: parametrized EN/HI/Hinglish matrix (yes / no / neither), following `tests/test_voice.py` pattern.

### P3b — Proceed/cancel in `AgentCore.run_turn` *(approach A — gate-safe, all channels)*
- Add one-shot `self.pending_suggestion: str | None` on `AgentCore`; set it at turn end whenever `_suggest_next_step` produced a suggestion (near 323-331). Tweak the suggestion prompt to phrase an **offer/question** ("…want me to … — karu?").
- At `run_turn` start, if `pending_suggestion` is armed AND the incoming `user_text` is short: `parse_yes` → re-submit as an explicit instruction ("Yes — go ahead: {pending_suggestion}") so the normal loop executes it **through the safety gate** (a CONFIRM-class action still pops its own confirmation); `parse_no` → one-line ack, no model call; neither → normal new turn. Always clear the flag after consuming (one-shot; the suggestion is already in history, so the model has context).
- Tests in `tests/test_agent_loop.py`: yes→tool executes via gate, no→ack + no tool, unrelated→normal turn, flag is one-shot.

### P3c — Conversation mode (follow-up window) in `voice/pipeline.py`
- Config `conversation: {enabled: true, window_s: 60, end_phrases: [...], cue: true}` (new top-level block, config.yaml after `persona:` ~116); pipeline reads `self.cfg`.
- At the `None` sentinel (`_respond` 475-478): if `conversation.enabled`, instead of `state = IDLE`, open a follow-up window — `_enter_listening(frames, source="followup")` (soft cue instead of the wake beep) → `state = LISTENING`. `run()` then calls `_listen`.
- In `_listen`, branch on `source == "followup"`: **no wake needed** (already listening); use `window_s` as the no-speech timeout (deadline = now + window_s); after STT: `is_end_phrase` → soft cue + `state = IDLE` (close session, no submit); `is_kill_phrase` → cancel + close; else → `_submit(text)` + RESPONDING (stay in session — the next turn's `None` sentinel re-opens the window). No speech within `window_s` → soft cue + IDLE.
- Baby never transcribes itself: the window opens only **after** playback fully ends, and `_enter_listening` drains the mic + resets VAD (370-372). Barge-in path untouched.
- Status events on open/close for the UI header LISTENING badge (`ui/server.py` /stats or a bus `status`).

### P3d — Wake word: multi-model + custom "jarvis" (decision 2)
- `voice/wakeword.py`: load a **list** — `[custom_jarvis.onnx (if present), hey_jarvis (pretrained)]` — so both score every chunk; `detected()`'s `max()` already wakes on either. Config: accept `wakeword_models: [..]` (list) alongside the existing single `wakeword_model`; threshold ~0.6 for the custom.
- Ship with pretrained `hey_jarvis` active; the custom slot is empty until the owner drops `models/jarvis.onnx`. **Owner manual step (Colab):** train single-word "jarvis" per `scripts/wakeword_training.md` — extra synthetic positives incl. Indian-English, threshold ~0.6, tune from accept/reject logs. Update `wakeword_training.md` with jarvis-specific guidance. PTT stays fallback; "Hey Baby" deferred.

### P3e — Docs + verify
- DECISIONS entry (approach-A proceed/cancel; multi-model wake; conversation default-on behind one flag). CHANGELOG P3 bullet. config.yaml `conversation:` block documented.
- `uv run pytest -q` + `test_safety.py` green + ruff clean. Commit `feat(voice): conversation mode + proceed/cancel flow (P3)`.

**Design decisions (locked):** proceed/cancel = re-submit "yes" as a turn (not a structured tool-exec) so the gate always applies; conversation ships **default-on** per spec but is one config flag to disable; multi-model wake needs no scoring change (`max()` already). Custom `jarvis.onnx` = owner's Colab step — all P3 **code** ships without it (pretrained `hey_jarvis` carries the demo).

**Accept:** "Jarvis" (or "Hey Jarvis") → **5-turn back-and-forth, zero wake words** → "Baby stop listening" closes with a cue; silence-timeout close verified. Proposal → "haan kar do" executes (gate still fires for CONFIRM); "nahi" drops; unrelated question just works. Baby never answers its own TTS; `test_intents.py` + proceed/cancel tests green; conversation mode fully disableable via config. False-accept sanity (owner, with the trained model): nearby chatter + a "Jarvis"-mention clip trigger rarely.

---

## P4 — Memory v2: budgeted context + cross-session RAG + clear/wipe (#3, #5) *(~1 week)*

Grounding (verified this session, file:line):
- **Memory:** `fact_vectors` vec0 (`memory/store.py:65-69` — `fact_id, embedding float[384] distance_metric=cosine`; brute-force `fact_embeddings` fallback :73-76); `_nearest` :81-102, `_insert_vector` :104-110; `add_fact`/`search`/`forget`/`list_facts`/`count_active` :114-234; e5-small 384-d, `query:`/`passage:` prefixes (`memory/embedder.py:12-13`, async, single encode lock). `store.init()` creates the vec table idempotently (:65). tools `remember/recall/forget` ALLOW (`tools/memory_tools.py:25-46`).
- **Agent:** `_loop` builds `[system(summary,memories,language)] + history + trailing-system` (`core/agent.py:250-271`); `history_limit=30` **uniform** (:123); facts via `store.search(user_text)` (:237); summary + `after_id=summarized_upto` skips folded turns (:232-236); `sanitize_messages` before dispatch (:276); post-turn `_maintenance` runs summarize+extract on `status==ok` (:507-521, spawned :215).
- **Dispatch seam:** `provider.chat(messages, tools, channel)` (`core/agent.py:431`); CloudRouter `_pick`s a tier then calls the concrete tier provider; mid-loop per-request fallback resends the **same** messages array; `_estimate_tokens` = chars/4 (`core/router.py:51-54`); `num_ctx` 8192.
- **DB:** `messages(id,conversation_id,role,content,turn_id,status,created_at)`; reads filter `status='ok'` (`get_messages` :230, `messages_since` :251, `get_history` :421); `conversations(summary,summarized_upto,extracted_upto)`; idempotent `_migrate` PRAGMA-table_info ALTER (`db/database.py:52-77`); backup via sqlite backup API (`scripts/migrate_v2_db.py:26-51`).
- **Surfaces:** `GET /memory`→`list_facts` (`ui/server.py:143-147`); `POST /conversation/new` (:127-141, no memory clear); **no** memory browser UI; confirm modal reusable (`ui/web/index.html:42-51`, `app.js:157-186`). Escape-hatch: `parse_game_command` anchored, short-circuits BEFORE the agent in ws_chat (:217-227) and voice `_listen`; **all channels funnel to `AgentCore.run_turn`** (cli/telegram/ui/voice). `core/intents.py` `parse_yes/parse_no/is_end_phrase`.

**Owner decisions (this session):**
- **RAG timing = live + nightly backfill:** embed each new `ok` message right after the turn (post-turn maintenance, off the critical path) so same-session AND prior-session recall both work; a nightly catch-up job + one-time backfill cover missed/old rows.
- **Budget = trim at the dispatch seam:** ONE context builder assembles history; a deterministic `trim(messages, budget)` runs at EACH provider dispatch — including mid-loop fallback and privacy-pin handoffs (re-trim the same array; the rolling summary substitutes for dropped turns). Per-model `max_history_tokens` (daily ~6K usable, NIM 24–32K). Trim rules: system prompt + tool schemas + summary + RAG block are **inviolable**; drop **oldest whole turns first**; never split a `tool_call`/`tool_result` pair; **never** rely on Ollama `num_ctx` truncation. Router states/ladder/bucket stay untouched — the only new seam is a `trim()` call where a provider is invoked (owner-authorized narrow exception to the frozen-router rule).
- **Wipe = true amnesia:** facts + message vectors + conversation summaries + **raw `messages`/turns**, then VACUUM; challenge-gated + audited (`audit_log` retained). Two guards: (a) wipe also moves/resets the embed watermark and drops raw turns so the **nightly reconciler can never re-embed pre-wipe content**; (b) wipe **flushes the live session** — reset in-RAM context + fresh conversation id — so Baby stops "remembering" immediately, not after a restart.

**Build (backup `baby.db` first — reuse `migrate_v2_db.py` backup pattern).** New behavior behind `memory.engine: v2` (v1 = today's behavior, one-line rollback); clear/forget/wipe controls always on.

### P4a — Store + DB foundation
- `message_vectors` vec0 (`message_id INTEGER PRIMARY KEY, embedding float[384] distance_metric=cosine`) + `message_embeddings` BLOB fallback, created idempotently in `store.init()` mirroring `fact_vectors`.
- `conversations.message_embedded_upto INTEGER DEFAULT 0` via the idempotent `_migrate` (`db/database.py:52-77`) — auto-applied at connect, no manual step.
- Store methods (mirror `_nearest`/`_insert_vector`): `embed_new_messages(conv_id)` (embed `status='ok'` user+assistant since the watermark with `passage:`, advance it — mirrors the extractor cadence/watermark), `search_messages(query, k=4)` (`query:` embed → `_nearest` over `message_vectors` → join messages for text+created_at+conversation_id, floor at `min_similarity`, exclude the current conv's most-recent rows already in raw history), `forget_last()` (deactivate the newest active fact), `delete_fact(id)` (hard-delete row + vector, for the UI browser), `wipe_all()` (delete facts + fact/message vectors + **raw `messages` rows** + clear `conversations.summary` and reset ALL watermarks incl. `message_embedded_upto`, VACUUM, retain `audit_log`; return counts — dropping raw turns + resetting the watermark is what stops the nightly reconciler re-embedding pre-wipe content).
- Tests `tests/test_memory_v2.py`: message-embed + watermark advance, `search_messages` retrieval + floor + current-conv exclusion, `forget_last`, `delete_fact`, `wipe_all` completeness (facts + all vectors + summaries + raw turns gone) **and** a forced nightly-reconciler run after a wipe re-embeds nothing (recall stays empty).

### P4b — Budget trim at the dispatch seam
- New pure `core/context.py::trim(messages, budget) -> list[dict]` beside `sanitize_messages`: pin index-0 system, the trailing system nudge, any summary/RAG system blocks; budget covers **history only** (tool schemas exempt); walk oldest→newest and drop **whole turns** (never split a `tool_call`+`tool_result` pair) until under `budget`; if all but the pinned head is dropped, the summary in the system prompt carries continuity.
- Reuse/relocate `core/router.py::_estimate_tokens` (chars/4) into `core/context.py`; no tiktoken.
- Per-model config `max_history_tokens` (`models.daily` ~6000, `models.nim_primary`/`nim_heavy` ~28000, `models.cloud` per Gemini). Seam: at each concrete provider invocation in the router (and the direct `local_primary` path), `trim(messages, that_model.max_history_tokens)` before `.chat()`. Mid-loop fallback re-trims the same array to the new brain's budget. `engine: v1` → trim is a no-op.
- Tests: pure `trim` matrix (drops oldest turn, keeps pinned system+summary, never splits a tool pair, budget-0 keeps only the pinned head); fallback re-trim to a smaller budget.

### P4c — Conversation-RAG injection + live/nightly embedding
- In `_loop` (`core/agent.py:230-251`), when `engine: v2`, also `rag = await store.search_messages(user_text)`; inject a compact dated `## Relevant past context` system block (k=4) into the **inviolable** head so `trim` never drops it.
- Live embedding: extend `_maintenance` (`agent.py:507-521`) with `await memory.store.embed_new_messages(conv_id)` — same background, post-`ok`-turn spot as summarize/extract.
- Nightly + backfill: a dedicated APScheduler job registered directly in `workers/scheduler.py::start()` (runs code, NOT a `schedules`-table model-prompt row) iterates conversations calling `embed_new_messages`; one-time `scripts/backfill_message_vectors.py` embeds all existing `status='ok'` messages (P2 status filter is inherent).
- Tests: RAG block injected + survives trim; maintenance embeds new messages; backfill only touches `ok` rows.

### P4d — Clear / forget / wipe commands (all channels)
- `core/intents.py`: `parse_memory_command(text) -> str|None` → `new_chat` | `forget_last` | `clear` | `wipe` (anchored, short, EN/HI/Hinglish: "new chat"/"nayi baat", "forget that"/"woh bhool jao", "clear conversation", "wipe all memory"/"sab kuch mita do"); plus `is_wipe_confirmation(text)` ("confirm wipe"/"haan sab mitao").
- Intercept at the TOP of `AgentCore.run_turn` (before the model loop) so ONE implementation reaches cli/telegram/ui/voice: `new_chat`/`clear` → `conversation_id = await db.create_conversation(channel)` + spoken ack (old conv stays, still RAG-searchable); `forget_last` → `store.forget_last()` + ack; `wipe` → set one-shot `self.pending_wipe`, reply the challenge, **no** model call; next turn, if `pending_wipe` and `is_wipe_confirmation` → `store.wipe_all()` + audit + **flush the live session** (`self.conversation_id = await db.create_conversation(channel)`; clear in-RAM `pending_*` + cached summary so Baby stops "remembering" immediately) + spoken confirm, else clear the flag. Publish `turn_start`/`token`/`turn_end` so every surface renders it. (Mirrors the `pending_suggestion` one-shot + game-mode escape hatch — deterministic, no gate bypass; wipe's two-step challenge + audit is its safety.)
- Tests in `tests/test_agent_loop.py`: each command dispatches with no model call; wipe is two-step (a single "wipe" never erases); confirm executes + audits; unrelated text clears `pending_wipe`.

### P4e — UI memory browser
- Endpoints (`ui/server.py`): `GET /memory` (exists) feeds a panel; `DELETE /memory/fact/{id}` → `store.delete_fact`; `POST /memory/wipe` (challenge token in body) → `store.wipe_all` + audit; `GET /conversations` list for browse/archive.
- `ui/web`: a Memory panel (fact list, per-row delete, active/forgotten shown) + a red **Wipe all** button opening a challenge modal (reuse the `#confirm-dialog` pattern with an explicit typed/second-step confirm). Vanilla JS, no new deps.

### P4f — Docs + verify
- DECISIONS entries: trim-at-dispatch-seam budget (owner-authorized router seam), live+nightly message embedding, wipe = true-amnesia + challenge, `memory.engine` flag. CHANGELOG P4 bullet. `config.yaml`: `memory.engine`, per-model `max_history_tokens`, RAG keys documented. `tests/manual/p4_memory_checklist.md`.
- `uv run pytest -q` + `test_safety.py` green + ruff clean. Commit `feat(memory): budgeted context + cross-session RAG + clear/wipe (P4)`.

**Accept:** reference a past-session topic by meaning ("us din wali EV comparison yaad hai?") → retrieved + used (same-session too, via live embedding). A long cloud turn carries deep history (verify the assembled/trimmed prompt size in logs); the same array on a local fallback is trimmed to ~6K with the summary substituting — never Ollama-truncated. Every clear/forget/wipe works by voice + text + UI; wipe needs the two-step challenge; post-wipe RAG + facts return empty and Baby says so — a forced nightly run after a wipe re-embeds nothing (watermark moved, raw turns gone), and the wiping session flips to a fresh conversation id immediately (no "remembering" until restart). `test_memory_v2.py` + command/trim tests green; safety green.

---

## P5 — Token telemetry (#8), soak & release ✅ CODE COMPLETE

Status: P5a–d done, committed `ea06254` (+ checklist `831d02f`), branch merged
up to master (`864db28`, v1.1.1 hotfix conflicts resolved). `675 passed`, safety
green, ruff clean, version `2.0.0`. **PR #2 ready for review, MERGEABLE/CLEAN.**
Remaining P5e (soak + merge + tag) is owner-only — see "Merge & release" below.

**All phases P0–P5 done.** Owner soak checklist: `tests/manual/p5_telemetry_checklist.md`.

### Context

v2 added conversation mode, DB hygiene, and memory-v2 — but Baby still can't tell
you what any of it *costs*. **Zero token usage is captured today.** Every
OpenAI-compatible response (NIM/OpenRouter, Gemini, Ollama) carries a `usage`
object with prompt/completion/total counts, and Baby throws it all away: `Chunk`
has no field for it, `accumulate_stream` never reads `event.usage`, no DB column
stores it, and `/stats` shows only turn counts + latency. P5 threads that number
end-to-end so the owner sees per-turn `↑prompt ↓completion` in the feed plus
session + today totals in the header — then soaks the whole v2 build for 2–3 days
and readies the release. This is the **final phase**; it finalizes `2.0.0`.

### Grounding (verified this session, file:line)

- **Loss point 1 — no field:** `Chunk` dataclass (`core/providers/base.py:20-29`) = `delta, tool_calls, done` only.
- **Loss point 2 — never read + skipped:** `accumulate_stream` (`base.py:48-77`) does `if not event.choices: continue` (skips the trailing `include_usage` chunk, which has **empty `choices`**) **and** `yield … done=True; return` on `finish_reason` (returns before that trailer arrives). Both must change: capture `event.usage` off *any* event, and yield the single `done` chunk **after** the stream drains, not on `finish_reason`.
- **Loss point 3 — providers don't ask:** `nvidia.py:55-78`, `gemini.py:77-100`, `ollama.py:33-67` all call `chat.completions.create(..., stream=True)` with **no** `stream_options`.
- **Loss point 4 — router transparent:** `RouterProvider.chat` (`router.py:233-277`) and `CloudRouter.chat` (`router.py:736-883`) forward chunks unchanged — no change needed; `CloudRouter._record_served` (`router.py:900-910`) already writes a per-stream audit row (pattern to mirror).
- **Loss point 5 — agent discards:** `_stream` (`agent.py:556-570`) returns `(text, tool_calls)`, drops usage. Extra generations that also spend tokens: `_final_answer` (`agent.py:586-602`), `_suggest_next_step` (`agent.py:604-636`). Turn end publishes `turn_end {reply,status,brain}` (`agent.py:235-238`) with `turn_id` in scope (`agent.py:182`).
- **Loss point 6 — no schema:** `audit_log` (`schema.sql:63-72`) is **tool-keyed, no turn_id** — wrong grain for per-turn tokens. `add_audit` (`database.py:151-164`). `_migrate` idempotent PRAGMA-`ALTER` loop (`database.py:52-77`); `schema.sql` `CREATE TABLE IF NOT EXISTS` runs on every connect, so a **new table** needs no migration script.
- **UI:** `/stats` (`ui/server.py:83-118`) returns model/router/latency/gauges; `bus.publish("turn_end", …, brain=…)`. Feed render `addFeedEntry`/`addBrainBadge` (`app.js:117-142, 38-55`); chat `turn_end` handler (`app.js:72-82`); `pollStats` every 5s (`app.js:246-259`); header gauges (`index.html:18-23`).

New behavior is **read-only telemetry** — no router/gate logic touched (the only provider-layer edit is adding `stream_options` + capturing a field the API already sends, which the frozen-router rule explicitly permits as "P5's `usage` read").

### P5a — Capture usage through the provider seam

- **`Chunk`** (`base.py`): add `usage: dict | None = None` (`{"prompt_tokens","completion_tokens","total_tokens"}`).
- **`accumulate_stream`** (`base.py`) — the one shared hot path, so change surgically and cover with tests:
  - Before the `if not event.choices` skip, capture usage off *any* event: `if getattr(event, "usage", None): usage = _usage_dict(event.usage)` (SDK object → plain dict, null-safe).
  - On `finish_reason`: **record** the assembled `tool_calls` into a local, do **not** `return` — let the loop keep draining so the empty-`choices` usage trailer is consumed.
  - After the loop: `yield Chunk(tool_calls=final_calls or [], done=True, usage=usage)` — exactly **one** `done` chunk, now carrying usage. Preserves text-order and tool-assembly semantics; only defers the `done` chunk by ≤1 iteration.
- **Providers**: add `stream_options={"include_usage": True}` to the `create(...)` call in `nvidia.py`, `gemini.py`, `ollama.py`. Guard per-provider with an attr/config flag `emit_usage` (default `True`) so a host that 400s on the param (possible for the Gemini compat endpoint — verify in soak) can be flipped off without code change; capture stays null-safe so "no usage" degrades to "no tokens shown", never a crash.
- **Tests** (`tests/test_providers.py` or `test_base.py`): a fake stream whose trailer has `choices=[]` + `usage=…` → the final `Chunk.done` carries the dict; a no-usage stream → `usage is None`; tool-call assembly + text order unchanged (regression on the reordered `done`).

### P5b — Aggregate per turn + persist

- **Agent aggregation:** reset `self._turn_tokens = {"prompt":0,"completion":0,"total":0}` at `run_turn` start; a helper `_accrue_tokens(chunk.usage)` sums into it. Call it in all three generation loops (`_stream`, `_final_answer`, `_suggest_next_step`) so a multi-round tool turn totals every model call. `_stream` returns usage too (3-tuple) — or simpler, each loop accrues directly and `_stream`'s signature is unchanged except it feeds the accumulator.
- **DB — new `usage_log` table** (`schema.sql`, keyed to P2's `turn_id`): `id, ts, conversation_id, turn_id, channel, brain_tier, brain_model, prompt_tokens, completion_tokens, total_tokens`. Auto-created on connect (`CREATE TABLE IF NOT EXISTS`), so no migration-script row — but add a note to `migrate_v2_db.py`.
- **DB methods** (`database.py`, mirror `add_audit`): `add_usage(conversation_id, turn_id, channel, brain, tokens)`; reads `usage_today()` (SUM + `GROUP BY brain_tier` where `date(ts)=date('now','localtime')`) and `usage_session(since_iso)` (SUM since a process-start ISO stamp).
- **Wire at turn end:** in `run_turn`'s `finally` (where `brain` is already snapshotted, `agent.py:235`), if `self._turn_tokens["total"]`, `await self.db.add_usage(...)`; and add `tokens=self._turn_tokens` to the `turn_end` bus payload so the UI updates live (no poll wait). Ollama/`daily` tier tokens are recorded + labeled "local — no quota" downstream.
- **Tests** (`test_agent_loop.py`, `test_db` area): `_turn_tokens` sums across two tool rounds; `run_turn` writes one `usage_log` row with the final brain; `turn_end` payload carries `tokens`; `usage_today` aggregates + groups by brain.

### P5c — UI: feed counts + header totals

- **Feed / bubble** (`app.js:72-82` `turn_end`): render a small `↑{prompt} ↓{completion}` line beside the brain badge (reuse the `brain-badge` styling); local brain shows the counts tagged "local".
- **Header** (`index.html:18-23` gauges row, `app.js:246-259` `pollStats`): `/stats` gains a `tokens` block `{session:{…}, today:{…}, by_brain:{…}}` from `usage_session`/`usage_today`; render **session + today totals** next to the gauges + a small per-brain breakdown (tooltip). Store the process-start ISO in the UI `ctx` at startup to bound `session`.
- **`ui/server.py`**: extend `/stats` (`ui/server.py:83-118`) with the `tokens` block; keep it a pure DB read (single source of truth). No new websocket kind — tokens ride the existing `turn_end`.
- **Tests** (`test_ui.py`): `/stats` returns a well-formed `tokens` block after a recorded turn; endpoint stays green with an empty `usage_log`.

### P5d — Docs + version + verify

- **Version:** bump `pyproject.toml:3` `2.0.0-dev` → `2.0.0`.
- **DECISIONS.md:** new `usage_log` table (turn-grain, not audit-grain) + why; `stream_options.include_usage` + null-safe capture + per-provider `emit_usage` guard; Ollama native eval counts labeled "local — no quota"; `accumulate_stream` deferred-`done` change.
- **CHANGELOG.md** P5 bullet; **README** v2 token-telemetry feature; **config.yaml** doc for `emit_usage` if surfaced; **`migrate_v2_db.py`** note that `usage_log` auto-creates.
- `uv run pytest -q` + `tests/test_safety.py` green + `uv run ruff check .` clean. Commit `feat(telemetry): per-turn token usage capture + UI totals (P5)`.

### P5e — Soak + release (owner-run)

- **Soak (owner, 2–3 days, everything on):** collect conversation-session lengths, false window-closures, new quarantine events (~0), RAG hit quality, **tokens/day vs provider dashboard**, unhandled exceptions (0). Verify each cloud provider actually emits `usage` (flip `emit_usage` off for any that 400s).
- **Full regression (Claude):** whole pytest suite (safety green) + `scripts/e2e_regression.py --with-project --fresh-conversation` + v1.1.0 manual demos (offline fallback, game mode, privacy pins) + Phase 1–4 demos.
- Write the soak summary into the PR; complete the PR checklist; mark **Ready for Review**. **Owner merges + tags `v2.0.0`** (Claude never merges).

**Accept:** every cloud call in the feed shows `↑prompt ↓completion`; the header shows session + today totals with a per-brain split; a recorded turn writes exactly one `usage_log` row keyed to its `turn_id`; local (Ollama) turns show counts tagged "local — no quota"; daily total matches the provider dashboard within reason; a provider that omits usage degrades to blank counts (no crash); soak summary in the PR; zero unhandled exceptions; `test_safety.py` + all regressions green.

---

## Verification (end-to-end)

- After **each** phase: `uv run pytest -q` green, `tests/test_safety.py` untouched + green, `uv run ruff check .` clean.
- P1: live "CPU temperature?" both with LHM up and killed.
- P2: migration on a **copy** of real `baby.db` + mid-turn kill/restart.
- P3: 5-turn no-wake-word live demo + false-accept sanity + `test_intents.py`.
- P4: cross-session recall demo + prompt-size logs + wipe-then-empty-RAG.
- P5: feed token counts vs provider dashboard + 2–3 day soak + full regression + v1.1.0 demos.
- E2E battery `scripts/e2e_regression.py --with-project --fresh-conversation` before Ready for Review.

## Standing constraints

Never bypass the safety gate; tests use dry_run. Never commit `.env` or key values; never log key values. Telegram answers only `TELEGRAM_CHAT_ID`; UI bound to 127.0.0.1. Zero commits to master; Tanishq merges — never Claude. No provider/router/safety-gate changes beyond P5's `usage` read and P4's owner-authorized `trim()` seam at provider dispatch — otherwise the v1.1.0 router (states/ladder/bucket) is frozen ground. New behavior behind flags (`conversation.enabled`, `memory.engine`). **v3 brain-UI is out of scope this branch.**

## Merge & release (owner-only — Claude never merges/tags)

After the 2–3 day soak passes (`tests/manual/p5_telemetry_checklist.md`):

```bash
# 1. final green gate on the branch
git switch feature/v2-conversational-reliability
git pull
uv run pytest -q && uv run pytest tests/test_safety.py -q && uv run ruff check .

# 2. merge PR #2 (squash keeps master history clean; --admin only if needed)
gh pr merge 2 --squash --delete-branch
#    (or on GitHub: "Squash and merge")

# 3. tag the release on the updated master
git switch master && git pull
git tag -a v2.0.0 -m "v2.0.0 — conversational & reliable (P0–P5)"
git push origin v2.0.0
```

Rollback after merge if a phase misbehaves: `conversation.enabled: false`
(P3), `memory.engine: v1` (P4), `telemetry.emit_usage: false` (P5) — all
one config line; or `git revert` the squash commit.
