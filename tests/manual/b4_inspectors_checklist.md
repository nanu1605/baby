# B4 manual acceptance — node inspectors + per-type controls

Owner-run. B4 adds click-a-node inspector drawers + additive controls: tool
enable/disable (schema-gated), best-brain boost, task cancel, scheduler run-now,
memory browse/wipe, deep-linking. Backend is **additive only** — the safety gate
is untouched and unbypassable. Automated coverage: `tests/test_tool_flags.py`,
gate-immutability cases in `tests/test_safety.py`, frontend `nodeEvents` vitest,
`ui/app` build + test. This is the live end-to-end pass.

Prereq: `config.yaml` → `ui.frontend: v3`; `npm --prefix ui/app run build`;
`uv run python run.py --ui`; open `http://127.0.0.1:8765/`. (A `baby.db` backup was
taken to `backups/baby-b4.db`; the new `tool_flags` table auto-creates on connect.)

---

## §1 — Drawer opens on any node (no empty drawers)

- [ ] Click each node type → a right-side drawer opens with **label, role, blurb**: a tool, a brain, `safety_gate`, `task_queue`, `scheduler`, a memory node, a voice node, `router`/`telegram`/`browser`/`screen`/`baby_core`.
- [ ] The clicked node gets a selection ring; the camera flies to it. Clicking empty space (or ✕) closes the drawer.

## §2 — Tool enable/disable (schema-gated)

- [ ] Open a tool drawer (e.g. `tool:get_time`). Toggle **Disabled**. It shows calls/error-rate/p50/p95 stats.
- [ ] Ask Baby to use that tool next turn → it does **not** call it (the schema is hidden from the model). Re-enable → it can call it again.
- [ ] The safety gate is unaffected either way (a disabled tool that is somehow invoked is still classified).

## §3 — Safety-gate immutability (hard rule)

- [ ] Open the `safety_gate` drawer → it shows the "**cannot be disabled or bypassed**" note and has **NO toggle**.
- [ ] `curl.exe -s -o /dev/null -w "%{http_code}" -X POST http://127.0.0.1:8765/api/tools/safety_gate/flag -H "Content-Type: application/json" -d "{\"enabled\":false}"` → **404** (the gate is not a tool). `router` likewise.

## §4 — Best-brain boost (one-turn, honest placement)

- [ ] The boost control appears ONLY on the **`brain:nim_heavy`** (heavy) drawer and as a ⚡ near the chat input — **not** on the local / Gemini / nim_primary drawers.
- [ ] Arm it → a "**boost armed**" chip shows near the input (with ✕ cancel). Send a turn → it prefers the strongest available brain; the chip **clears after that one turn**.
- [ ] **Privacy still wins:** arm the boost, then send a turn that reads a private file (pinned/local content) → it stays on the **local** brain (the boost is subordinate to the privacy pin), and the reply is unaffected. Arming shows as an `explicit_request` row in the audit log.

## §5 — Task queue + scheduler controls

- [ ] Start a background task (ask Baby to do something long). Open the `task_queue` drawer → the task lists with running/queued counts; **cancel** it → it stops.
- [ ] Open the `scheduler` drawer → upcoming jobs list (`morning-briefing`, any schedules). **Run now** on a job → it fires immediately (watch the activity feed / a toast).

## §6 — Memory node

- [ ] Open a memory node (`mem_facts`) drawer → fact count + browse list. Delete a fact → it disappears.
- [ ] Wipe requires typing `WIPE` (same challenge as the 🧠 dialog); wrong text is refused.

## §7 — Node-filtered events + deep-link

- [ ] A tool/brain drawer shows a **Recent activity** list filtered to that node.
- [ ] Visit `http://127.0.0.1:8765/#node/tool:run_shell` directly → the app focuses that node and opens its drawer. Selecting a node updates the URL hash.

## §8 — /classic unaffected

- [ ] `http://127.0.0.1:8765/classic` still works (the new endpoints/table are additive).

## §9 — Gates

- [ ] `npm --prefix ui/app run build` + `npm --prefix ui/app test` green.
- [ ] `uv run pytest -q` green; `uv run pytest tests/test_safety.py -q` green (gate immutability); `uv run ruff check .` clean.
- [ ] `git branch --show-current` = `feature/v3-brain-ui`. Zero commits on master.

---

**Restore after:** re-enable any tools you disabled; disarm any boost; set
`ui.frontend` to preference. `/classic` remains available.
