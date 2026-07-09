# B7 manual acceptance — polish, perf, soak & release

Owner-run. B7 is the release phase: the code-complete polish (responsive, WS
resilience, long-session hygiene, docs) is committed and the PR stays **Draft**.
This checklist gathers the owner-run gates — the 3-day soak, the perf
measurements, the speaker FAR/FRR report, and the full regression — that decide
whether `voice.speaker_verify.enabled` flips on and when the PR goes Ready.

Automated coverage already green (executor ran): `npm --prefix ui/app run build`
+ `npm --prefix ui/app test` (store hygiene/WS reducers), `uv run pytest -q`,
`tests/test_safety.py`, `uv run ruff check .`.

---

## §1 — Responsive (drawers over graph)

- [ ] Desktop: layout unchanged — graph centerpiece + inline right panel; collapse
  (`›`) / expand (`‹`) still work.
- [ ] Narrow the window to ~375px (or open on a phone over Tailscale,
  [docs/TAILSCALE.md](../../docs/TAILSCALE.md)): the graph goes full-bleed and stays
  **pannable + zoomable**; the chat/activity panel becomes a right slide-over drawer
  with a tap-away backdrop; the header gauges hide.
- [ ] The inspector opens **full-width**; the "Search the brain…" omnibox is
  full-width; both are usable one-handed.
- [ ] `prefers-reduced-motion` (OS setting): the streaming cursor stops blinking and
  panel slides are instant.

## §2 — WS resilience (kill / recover, all 3 channels)

- [ ] With a turn mid-stream, kill the backend (Ctrl-C `run.py`): a **reconnect pill**
  appears in the header, the streaming bubble **finalizes** (no infinite blinking
  cursor) and a "Connection lost — reconnecting…" line shows; the chat + activity
  panels read "Backend unreachable — reconnecting…" when empty.
- [ ] Restart the backend: all three sockets recover on their own (no manual reload);
  the pill clears; `/ws/state`, chat and activity resume.
- [ ] Fresh load with the backend down shows "connecting…" (not "reconnecting…").

## §3 — Perf gate (measured — the deferred B2/B3/B5 gates, gathered here)

Record with `ui.frontend: v3`, the local 9B loaded, and **`performance_mode` OFF**
(it is a user opt-in, never a default-on ship for the centerpiece). Budget:
sustained **< ~10% GPU / ~5% CPU**, 60fps while active / ~20–24fps idle, graceful
degradation. (Fills the blanks left at
[b3_live_graph_checklist.md](b3_live_graph_checklist.md) §5 and
[b5_search_checklist.md](b5_search_checklist.md) §7.)

- [ ] Idle throttled (gauge breathing, no traffic): GPU ≈ ____ % · CPU ≈ ____ % · fps ≈ ____
- [ ] A live turn pulsing (voice turn, full path): GPU ≈ ____ % · CPU ≈ ____ % · fps ≈ ____
- [ ] Omnibox open over an idle graph: within budget.
- [ ] If idle-throttled misses budget with `performance_mode` OFF → **escalate to owner**
  before any fallback (do not silently default `performance_mode` on).

## §4 — Long-session hygiene (heap stable over hours)

- [ ] Drive for a long session (or leave the tab open through the soak). In DevTools:
  the JS heap is stable over hours (no monotonic climb); take two snapshots hours apart.
- [ ] The transcript caps at 300 messages, the toast stack at 5, the event ring at 500
  (older entries drop; the newest always survive).

## §5 — The 3-day soak = speaker score-collection window

Run `ui.frontend: v3` as the daily driver for ~3 days.

- [ ] Enroll: `uv run python scripts/enroll_voice_v2.py` (guided positions); optionally
  a non-owner test profile with `--label guest1`.
- [ ] `voice.speaker_verify.enabled: true`, `mode: observe` — mechanism on, gate OFF:
  every utterance is scored + logged, nothing is ever blocked. Speak normally for days.
- [ ] Have another person speak during a marked window (note the timestamps).
- [ ] Build the report: `uv run python scripts/speaker_report.py --since <soak-start>`
  (add `--far-since/--far-until` for the non-owner window). Per-model FAR/FRR + a
  recommended `(model, accept, reject)`.
- [ ] **Flip decision:** set `enabled: true` + `mode: chat_only` + the winning
  `model`/thresholds **only if** owner **FRR ≤ 2% AND 0 non-owner gated approvals**;
  else leave `enabled: false` and record the findings in `DECISIONS.md`. Update the
  `config.yaml` model/threshold comments to the chosen values.

## §6 — Full regression (owner-run; opens a browser + may speak)

- [ ] Backend + Ollama up. `uv run python scripts/e2e_regression.py --with-project
  --fresh-conversation` → `bench_results/E2E_REPORT.md` green (this opens a real
  Chromium window and may speak announcements).
- [ ] v2 demos: conversation mode, proceed/cancel, wipe challenge, token telemetry.
- [ ] v1.1 demos: offline fallback (pull Wi-Fi), game mode, privacy pins.
- [ ] `http://127.0.0.1:8765/classic` still fully works; `ui.frontend: classic` rolls
  the whole UI back.

## §7 — Release

- [ ] `git branch --show-current` = `feature/v3-brain-ui`; zero commits on `master`.
- [ ] PR §12 checklist complete; perf numbers + FAR/FRR report attached to the PR.
- [ ] Drop the screenshots/GIF into `docs/img/` (see [docs/img/README.md](../../docs/img/README.md)).
- [ ] **Owner only:** flip the PR **Ready for Review**, merge, tag **`v3.0.0`**.

---

**Rollback (config-first):** `ui.frontend: classic` (whole UI),
`voice.speaker_verify.enabled: false` (B6), tool re-enable via `tool_flags`. Or
`git revert` the squash. `/classic` stays live the whole branch + ≥1 release after.
