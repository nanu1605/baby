# P4 Manual Acceptance Checklist — Memory v2

Covers V2 items #3 (bigger cross-session memory) and #5 (clear/forget/wipe).
Requires `memory.engine: v2` (config.yaml, default). **Back up `baby.db` first**
(`backups/baby-<date>.db`) and run the one-time backfill so existing history is
searchable: `uv run python scripts/backfill_message_vectors.py`.

## 0. Automated gate (must be green)

- [ ] `uv run pytest -q` → all pass.
- [ ] `uv run pytest tests/test_memory_v2.py tests/test_context_trim.py tests/test_safety.py -q` → green.
- [ ] `uv run ruff check .` → `All checks passed!`.

## 1. Cross-session RAG (#3)

- [ ] In one chat, discuss a distinctive topic (e.g. "compare the Nexon EV vs
      Punch EV"). Say **"new chat"** to rotate the conversation.
- [ ] In the fresh chat ask by meaning, no keywords: **"us din wali EV
      comparison yaad hai?"** → Baby recalls it (the reply reflects the earlier
      exchange, drawn from the injected "Relevant past context" block).
- [ ] Same-session recall: it works immediately after a turn (live embedding),
      not only after the nightly run.
- [ ] Baby does NOT hallucinate repetition — an unrelated new question is
      answered fresh, not "as we discussed".

## 2. Per-brain context budget (trim)

- [ ] Hold a long conversation (20+ turns) on the cloud brain, then ask
      something that needs an early detail → answered (deep history carried).
- [ ] Force a local turn (game mode OFF + a privacy-pinned request, or offline)
      → the reply stays coherent via the rolling summary; check the log/prompt
      size shows history trimmed to ~6K, NOT an Ollama context overflow.
- [ ] Rollback check: set `memory.engine: v1`, restart → classic behavior
      (uniform history, no RAG block). Restore `v2` after.

## 3. Clear / forget (voice + text + UI)

- [ ] **"new chat" / "nayi baat"** (voice and typed) → fresh conversation; the
      old one is gone from context but still RAG-searchable.
- [ ] Tell Baby a fact ("remember my gym is Monday"), then **"forget that"** /
      **"woh bhool jao"** → it drops the newest fact and says so.
- [ ] These run with NO model call (instant, deterministic) on every channel.

## 4. Wipe all memory (#5) — destructive, challenge-gated

- [ ] Voice/text: say **"wipe all memory"** → Baby asks for confirmation and does
      NOT erase. Say something else ("never mind") → nothing wiped.
- [ ] Say **"wipe all memory"** again, then **"confirm wipe"** / **"haan sab
      mitao"** → everything erased; Baby confirms it remembers nothing.
- [ ] A bare **"yes"/"haan"** after the challenge does **NOT** wipe (only the
      explicit confirm phrase does).
- [ ] Post-wipe: recall returns empty ("I don't have anything on that"), and the
      live session is fresh WITHOUT a restart.
- [ ] Reconciler guard: after a wipe, run
      `uv run python scripts/backfill_message_vectors.py` (stands in for the
      nightly job) → it embeds 0, and recall stays empty (no pre-wipe content
      resurrected).
- [ ] Audit: the wipe appears in the activity feed / `audit_log` as
      `wipe_memory` with counts.

## 5. UI memory browser

- [ ] Web UI (`http://127.0.0.1:8765`): click **🧠** → the dialog lists learned
      facts (forgotten ones dimmed/struck-through) with a count.
- [ ] **✕** on a fact deletes it permanently; the list refreshes.
- [ ] **Wipe all…** prompts for a typed **WIPE**; anything else cancels. Typing
      WIPE erases everything and the list empties.

---

Done when 0–5 pass. Rollback for the whole phase: `memory.engine: v1` (config)
or restore the pre-P4 `baby.db` backup. Note: a wipe is irreversible at runtime
— only the manual backup recovers pre-wipe data.
