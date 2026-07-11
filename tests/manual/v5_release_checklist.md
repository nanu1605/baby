# V5 manual acceptance — soak, perf gates & release

Owner-run, real box (the 5060 Ti). V5 is the release phase: the always-green work —
the version bump to **4.0.0**, the release docs, and this checklist — is committed and
the PR stays **Draft**. This gathers the owner-run gates that decide whether v4 ships:
the perf measurements across all three tiers, cold-start, long-session heap, the 3-day
soak, the disruptive regression + demos, and the Tailscale/phone pass. It closes the
whole v4 branch (V0 native shell → V4 motion), so it also re-runs the earlier phases'
owner acceptance end-to-end inside the native shell.

Automated coverage already green (executor ran): `npm --prefix ui/app run build` +
`npm --prefix ui/app test` (governor / sphere / motion / amplitude pure logic),
`cargo build --release` (the shell binary), `uv run pytest -q`, `tests/test_safety.py`,
`uv run ruff check .`, and the 4.0.0 version bump across the app + shell tracks. This is
the perf / soak / regression pass automation can't cover.

Prereq: on branch `feature/v4-native-3d-brain`. `config.yaml` → `ui.brain: 3d` (the
code-default; set it explicitly) and `ui.shell: native`. Build then launch the native
app: `npm --prefix ui/app run build` → run the shell
`ui/shell/src-tauri/target/release/baby-shell.exe` (it attach-or-spawns the backend), or
for the browser path `uv run python run.py --all` → open `http://127.0.0.1:8765/`. Have a
GPU/CPU monitor ready (Task Manager Performance or the browser task manager), DevTools for
§3, and a mic + `--voice` for the amplitude/soak items. See also
[v3_sphere_checklist.md](v3_sphere_checklist.md) (the 3D sphere) and
[v4_motion_checklist.md](v4_motion_checklist.md) (the motion system) for the per-phase
detail this release pass rolls up, and [b7_release_checklist.md](b7_release_checklist.md)
for the v3 release-phase template.

---

## §1 — Perf gate (measured — all three tiers, 9B loaded AND unloaded)

The contract: full3d holds **60 fps with `performance_mode` OFF** (a user opt-in, never a
default-on ship for the centerpiece). The governor's `⚙` tier chip (top-right of the
header) appears only when it has demoted below full3d; `/stats` `render.{target_fps,tier}`
reports the config ceiling.

- [ ] **full3d, 9B unloaded** (cloud turn, sphere at full quality — bloom + particles):
  a live turn firing → fps ≈ ____ · GPU ≈ ____ % · CPU ≈ ____ % · VRAM delta ≈ ____
- [ ] **full3d → lite3d, 9B loaded** (force the local model resident — a privacy-pinned
  or otherwise locally-routed turn; note game mode does the OPPOSITE, it *unloads* the 9B
  to free the GPU): the watchdog demotes; the `⚙` chip shows `lite3d`; bloom + particles
  shed; fps holds 60 → fps ≈ ____ · GPU ≈ ____ % · VRAM delta ≈ ____
- [ ] **2d floor** (`render.tier: 2d` or `ui.brain: 2d`): the v3 canvas graph renders;
  fps ≈ ____ ; the sphere unmounts cleanly (no WebGL context left running).
- [ ] Idle (gauge breathing, no traffic): the render clock throttles → fps ≈ ____ ·
  GPU ≈ ____ % · CPU ≈ ____ %.
- [ ] If full3d misses 60 fps with `performance_mode` OFF → **escalate to owner** before
  any fallback (do not silently default `performance_mode` on — release blocker).

## §2 — Cold-start (native shell, clean profile)

- [ ] Fully quit Baby (tray → "Quit Baby (app)"; confirm no `baby-shell.exe` / `pythonw`
  left). Launch `baby-shell.exe`: a splash shows, the backend attach-or-spawns, the window
  paints the UI. Spawn → first-window-paint ≈ ____ s.
- [ ] The spoken **"Baby ready"** cue fires once the model is warm (honors
  `startup.wait_for_model_s`).
- [ ] Second launch of `baby-shell.exe` **focuses the existing window** (single-instance),
  does not open a second window or a second tray icon.

## §3 — Long-session hygiene (heap stable over hours)

- [ ] Drive for a long session (or leave the app open through the soak). In DevTools
  (the shell's webview or the browser): the JS heap is stable over hours (no monotonic
  climb) — take two snapshots hours apart.
- [ ] The transcript caps at 300 messages, the toast stack at 5, the event ring at 500
  (older entries drop; the newest always survive).
- [ ] No WebGL context leak: after forcing several tier demotes/promotes and a
  context-loss/recovery, GPU memory returns to baseline (no accumulation).

## §4 — 3-day soak (routing + stability)

Run the native app as the daily driver for ~3 days.

- [ ] Build the soak report: `uv run python scripts/soak_report.py --since <soak-start>`
  → routing-stability table (routed / served / first-token p50/p95 / voice dead-air /
  unhandled-exception count). **Unhandled-exception target: 0.**
- [ ] `baby.log` has no unhandled tracebacks across the window; the tray dot stayed
  green/amber (no sustained red).
- [ ] If `voice.speaker_verify.enabled`, the soak doubles as the FAR/FRR window
  (`scripts/speaker_report.py --since <soak-start>`) — record findings; the flip decision
  is unchanged from v3 (b7 §5).

## §5 — Full regression + demos (opens a browser + may speak)

- [ ] Backend + Ollama up. `uv run python scripts/e2e_regression.py --with-project
  --fresh-conversation` → `bench_results/E2E_REPORT.md` green (T01–T16; opens a real
  Chromium window and may speak). The safety gate stays in **enforce** the whole run and
  is never bypassed; T10's confirm-class action is denied by default (or add
  `--approve-confirms` to approve it).
- [ ] v2 demos: conversation mode, proceed/cancel, wipe challenge, token telemetry.
- [ ] v1.1 demos: offline fallback (pull Wi-Fi → `router→brain:daily` lights the local
  brain), game mode (ghosts the local 9B), privacy pins. (See [DEMO.md](../../DEMO.md).)
- [ ] Browser + TTS beats from `DEMO.md` run from **inside the native shell** — screen
  awareness, the close-Chrome gated command (confirm fires instantly), spoken announces.

## §6 — Tailscale / phone

- [ ] Reach `http://<tailscale-ip>:8765/` from the phone
  ([docs/TAILSCALE.md](../../docs/TAILSCALE.md)): the UI loads; the graph is
  pannable/zoomable; drawers work one-handed. **Or** record this as documented
  desktop-first if the phone pass is out of scope for the release.

## §7 — Rollback (config-first, one line each)

- [ ] `ui.shell: browser` — the native shell stays closed; the browser UI at
  `127.0.0.1:8765` is untouched.
- [ ] `ui.brain: 2d` — instantly restores the v3 canvas graph (no sphere).
- [ ] `render.tier: 2d` forces the floor; `performance_mode` forces lite.
- [ ] `http://127.0.0.1:8765/classic` still fully works; `ui.frontend: classic` rolls the
  whole UI back to the vanilla panel.

## §8 — Release

- [ ] `git branch --show-current` = `feature/v4-native-3d-brain`; **zero commits on
  `master`**; working tree clean.
- [ ] `ui/shell/package-lock.json` is **tracked** (committed alongside the manifest), and
  `config.yaml` is **not** in the diff (owner keeps the local edit uncommitted).
- [ ] The PR §checklist complete; perf numbers + soak summary attached to the PR;
  screenshots/GIF in `docs/img/` (see [docs/img/README.md](../../docs/img/README.md)).
- [ ] **Owner only:** flip the PR **Ready for Review**, merge, and tag **`v4.0.0`**
  (annotated, mirroring v1.1.1 / v2.0.0):
  `git tag -a v4.0.0 -m "v4.0.0 — native app + 3D neural brain"` on the merge commit.

---

**Perf numbers recorded here + in the PR.** If full3d misses 60 fps with
`performance_mode` OFF, or the confirm dialog is ever gated behind an animation, or the
soak shows an unhandled-exception count above 0, that is a release blocker — escalate
before flipping the PR Ready. Rollback is config-first (§7); `/classic` and the
`127.0.0.1:8765` browser UI stay live the whole branch + ≥1 release after.
