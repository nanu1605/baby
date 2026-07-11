# V5 manual acceptance — chat history & default cloud mode

Owner-run. V5 adds a chat-history sidebar (browse / open read-only / resume / rename /
archive / delete) over additive read endpoints, and boots Baby in cloud (game) mode by
default so the local 9B stays unloaded at launch. Automated coverage is already green:
`uv run pytest` (incl. `tests/test_conversations.py`, the `test_memory_v2.py`
delete-proof, `test_readiness.py` cloud-mode), `tests/test_safety.py` (untouched),
`uv run ruff check .`, and `ui/app` `npm run build` + `npm test`. This is the real-box
pass automation can't cover: nvidia-smi VRAM, the privacy-pin capture-mock, the live
sidebar flows, and the 3-day soak.

Prereq: on branch `feature/v5-chat-history`, `config.yaml` → `ui.history: on` (or
unset — code-default on) and `startup.cloud_mode: true` (or unset — code-default on),
with **≥1 cloud key in `.env`** (default cloud mode needs it). **Back up `baby.db` →
`backups/baby.db.v5bak` with Baby stopped, and confirm the copy opens, before the first
v5 boot** (the `title`/`archived` migration is additive but the backup lets you roll the
whole DB back). Build then start: `npm --prefix ui/app run build` →
`uv run python run.py --all` → open `http://127.0.0.1:8765/`. Keep a GPU monitor
(`nvidia-smi`) + DevTools ready.

---

## §1 — History sidebar lists real chats

- [ ] The left **Chats** sidebar lists your real past UI conversations, newest activity
  first, each with a derived title + relative time + message count.
- [ ] Empty conversations (a bare "new chat" you never used) do **not** appear; neither
  do voice / Telegram / scheduler threads (UI channel only).
- [ ] The chat currently in the panel is **highlighted**.
- [ ] Toggle **Show archived** → archived chats appear with an "archived" badge; untoggle
  → they hide again.

## §2 — New chat, view-only, resume

- [ ] Click **＋ New chat** → the transcript clears; after you send a message, the prior
  chat drops into the list.
- [ ] Click a past chat → it opens **read-only** (its real messages; quarantined/failed
  turns absent); the composer is replaced by a **"Viewing a past chat"** bar.
- [ ] **Return to live** → back to the current conversation + composer.
- [ ] **Resume here** on a viewed chat → it continues in the live session; the next turn
  answers with that conversation's context rehydrated (and stays within the brain's
  budget — no context-overflow error).

## §3 — Search deep-link (v3 loop closed)

- [ ] Open the omnibox (Ctrl/⌘-K), search a phrase from an old chat, click the
  **Conversations** hit → that conversation opens read-only in the panel (not just a
  graph-node fly-to).

## §4 — Rename / archive / delete

- [ ] Hover a row → **✎ / 🗄 / 🗑** appear. **Rename** → the title persists (reload to
  confirm). **Archive** → it leaves the default list (find it under Show archived);
  **unarchive** brings it back.
- [ ] **Delete** a chat (confirm) → it's gone from the list. Then search a phrase that
  was only in that chat → **no conversation hit** (the RAG vectors + FTS were purged; a
  deleted chat can't resurface). Deleting the *active* chat drops you into a fresh one.

## §5 — No phantom brain pulses on switching

- [ ] Watch the 3D brain while you open / switch / rename / delete chats → **no neuron
  firing** from those UI actions (pulses only fire on real turns). Honest-data intact.

## §6 — Default cloud mode at boot

- [ ] Fresh boot → `nvidia-smi` shows the local 9B **not loaded** (~5.5 GB free); the
  "Baby ready" line notes **cloud mode (local brain idle)**; the local brain node renders
  **ghosted**.
- [ ] A normal chat turn answers **via cloud** (badge shows a cloud brain); VRAM stays
  free while chatting.
- [ ] A **privacy-pinned** turn ("read my notes file", "run a shell command") stays
  **local** — verify with the v2/NIM capture-mock that the pinned file/shell bytes never
  appear in a cloud-bound payload (accepting a one-time cold local load).
- [ ] Toggle **game mode OFF** → the local brain loads in the background and re-announces
  **"Baby ready"**; toggle back ON → it unloads.
- [ ] **Offline / no-key at boot** → Baby still starts and a normal turn returns an honest
  "cloud unreachable" message (not a silent dead end); pinned turns still run local.

## §7 — Rollbacks (config-first, one line each)

- [ ] `ui.history: off` → the sidebar disappears; the rest of the UI is unchanged.
- [ ] `startup.cloud_mode: false` → the local 9B warms at boot again (pre-v5); a normal
  turn can route local with no cloud key.
- [ ] `/classic` and `http://127.0.0.1:8765/` stay live throughout.

## §8 — Gates + soak

- [ ] `uv run pytest -q` green; `uv run pytest tests/test_safety.py -q` green (untouched);
  `uv run ruff check .` clean; `npm --prefix ui/app run build` + `npm test` green.
- [ ] Branch guard: `git branch --show-current` = `feature/v5-chat-history`; zero commits
  to master; `git status --porcelain config.yaml` clean (never committed); no secrets in
  the diff.
- [ ] 3-day soak (`scripts/soak_report.py --since <date>`): unhandled-exception count **0**;
  routing stable with the local brain idle most of the time.

---

**A deleted chat resurfacing in `/api/search`, or pinned file/shell bytes appearing in a
cloud-bound payload under default cloud mode, is a release blocker — escalate.** Once this
passes, flip the PR **Ready**, merge, and tag `v5.0.0`. Cross-links:
[b7_release_checklist.md](b7_release_checklist.md) ·
[v3_sphere_checklist.md](v3_sphere_checklist.md) ·
[v4_motion_checklist.md](v4_motion_checklist.md).
