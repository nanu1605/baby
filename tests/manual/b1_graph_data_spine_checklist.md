# B1 manual acceptance — backend graph data spine

Owner-run. B1 ships the read-only data layer the graph is built on: `/api/graph`
(topology), `/api/nodes/{id}/stats`, `/api/search`, and the synthesized `/ws/state`
gauge stream — plus additive event fields (`source`/`target`/`turn_id`) and an
additive `audit_log.duration_ms` column. **No visual change** and **frozen ground:
no router/gate/provider logic touched.** Automated coverage: `tests/test_graph_api.py`,
`tests/test_event_attribution.py`, `tests/test_search.py`, `tests/test_ui.py`
(`ws_state` snapshot). This checklist is the live end-to-end pass.

Prereq: on branch `feature/v3-brain-ui`. Backup `baby.db` before first run of any
migration path (`copy baby.db backups\baby-b1.db`; verify the copy opens). Start the
stack: `uv run python run.py --ui`. Endpoints answer regardless of `ui.frontend`
(they are `/api/*`, not the SPA). Use `curl.exe` (not the PowerShell `curl` alias) or
a browser tab.

---

## §0 — Topology: `GET /api/graph`

- [ ] `curl.exe http://127.0.0.1:8765/api/graph` → JSON `{nodes, edges}`, HTTP 200.
- [ ] Fixed subsystems present: `baby_core`, `router`, `safety_gate`, `mem_facts`,
      `mem_rag`, `mem_summaries`, `voice_wake`, `voice_vad`, `voice_stt`, `voice_tts`,
      `speaker_verify`, `task_queue`, `scheduler`, `telegram`, `browser`, `screen`.
- [ ] Brain nodes match `config.yaml` lineup: `brain:daily`, `brain:nim_primary`,
      `brain:nim_heavy`, `brain:backstop` — each `label` = the configured model id.
- [ ] Tool nodes auto-derived: one `tool:<name>` per registered tool, `blurb` = the
      tool's schema description (not empty). Count matches `registry.schemas()`.
- [ ] Every node has `{id, type, label, group}`; `group` ∈ {voice, brains, tools,
      memory, infra}. No duplicate ids.
- [ ] Edges reference only ids that exist in `nodes` (no dangling edge). Spot-check the
      real call path exists: `voice_stt→router`, `router→brain:*`, `brain:*→tool:*`,
      `*→safety_gate` before a tool, `brain:*→mem_*`, `scheduler→task_queue`.

## §1 — Node stats: `GET /api/nodes/{id}/stats`

Pick real ids from §0's output.

- [ ] `tool:<name>` (e.g. one you've actually run) → `{type:"tool", calls/error rate/
      p50/p95/last}`. A tool never called → zeroed/empty stats, **not** a 500.
- [ ] `brain:daily` → `{type:"brain", latency_ms:{p50,p95}, tokens:{...}, turns,
      current, router_state}`. `latency_ms` is `null`/`null` until that brain has served
      a turn — no crash on empty samples.
- [ ] `task_queue` → `{type:"infra", running, queued, tasks:[…]}`.
- [ ] `scheduler` → `{type:"infra", jobs:[{id, next_run}]}` (jobs may be empty).
- [ ] `mem_facts` → `{type:"memory", facts:<int>}`.
- [ ] Unknown id (e.g. `nonsense`) → `{type:"subsystem"}` minimal payload, HTTP 200 —
      **never a 404/empty drawer.**

## §2 — `audit_log.duration_ms` populated

- [ ] Send a chat message that fires a tool (e.g. ask Baby to run a harmless command).
- [ ] `curl.exe http://127.0.0.1:8765/api/nodes/tool:<name>/stats` → `p50`/`p95` now
      non-null for that tool (a fresh timing was captured).
- [ ] Old pre-B1 audit rows keep `duration_ms` NULL and are excluded from percentiles
      (no error, percentiles reflect only timed rows).

## §3 — Search: `GET /api/search`

- [ ] `curl.exe "http://127.0.0.1:8765/api/search?q="` (empty) → `{query:"", groups:{
      facts:[], conversations:[], activity:[], tasks:[]}}`, HTTP 200.
- [ ] Search a word you know is in a recent conversation → appears under
      `groups.conversations`, each result `{type, snippet, ts, node_id}`.
- [ ] Search a stored fact keyword → under `groups.facts`, `node_id:"mem_facts"`.
- [ ] Search a tool name you've run → under `groups.activity`, `node_id:"tool:<name>"`.
- [ ] Search a task title → under `groups.tasks`, `node_id:"task_queue"`.
- [ ] **Quarantine safety:** a message from a failed/quarantined turn (`status!='ok'`)
      never surfaces in `groups.conversations`. (Covered by `test_search.py`; spot-check
      if you have a quarantined row.)
- [ ] **Injection safety:** `q` with FTS metacharacters (e.g. `"a* OR b"`, quotes,
      parens) returns results or empty — **never** a 500 / `OperationalError`.

## §4 — `/ws/state` synthesized gauge

The pipeline only has IDLE/LISTENING/RESPONDING — `/ws/state` **synthesizes**
thinking/speaking/executing from bus events. Verify in the browser: open
`http://127.0.0.1:8765/`, devtools Console, paste:

```js
const s = new WebSocket(`ws://${location.host}/ws/state`);
s.onmessage = e => console.log(JSON.parse(e.data));
```

- [ ] Immediately logs an **initial snapshot** `{state, router, game_mode}` on connect.
- [ ] Send a chat turn. Observe the timeline: `idle → thinking` (turn_start) →
      `speaking` (first token) → `executing` (if a tool fires) → back to `thinking`/
      `speaking` → `idle` (turn_end).
- [ ] Toggle game mode → next `/ws/state` message carries the updated `game_mode`.
- [ ] `router` field mirrors the active brain's health (matches the header router dot).

## §5 — `/classic` unaffected by enriched events

- [ ] `ui.frontend: classic` (or open `/classic` directly). Send a chat turn — normal
      streaming reply. The additive `source`/`target`/`turn_id` keys ride in the WS
      payload; the vanilla UI ignores unknown keys and renders exactly as before.
- [ ] Activity feed at `/classic` still shows tool/router events with no missing fields
      or JS console errors.

## §6 — Gates

- [ ] `uv run pytest -q` green; `uv run pytest tests/test_safety.py -q` green (untouched);
      `uv run ruff check .` clean.
- [ ] `git branch --show-current` = `feature/v3-brain-ui`. Zero commits on master.
- [ ] `git status` clean except intended B1 changes; no `.env`/keys staged.

---

**Restore after:** set `ui.frontend` back to preference (`classic` until B2 parity).
`baby.db` unchanged except the additive `duration_ms` column + FTS mirror tables
(all additive — `/classic` and v2 behavior intact).
