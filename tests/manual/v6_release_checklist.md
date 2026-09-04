# v6.0.0 release checklist

Everything here is **owner-run**. The dev box cannot validate a stranger's first
launch, and nothing in this file is something Claude does: the merge, the tag, and
the publish are yours.

Automated gates (`pytest`, `vitest`, `ruff`, `cargo check`, a real `tauri build`)
are already green on the branch — this covers only what a machine in this repo
cannot prove.

---

## 0. Blockers before any of this matters

- [x] **`LICENSE` file exists** — MIT, which is OSI-approved, so a public
      release is meaningful and SignPath Foundation's free signing is now
      applicable. Confirm the copyright holder line reads the way you want it to
      before publishing; it is the one thing here I picked on your behalf.
- [ ] Back up `baby.db` and your local `config.yaml` before installing any build
      on your own machine.

## 1. Build the artifact

- [ ] **Build the SPA first.** Nothing in the shell's build does it —
      `tauri.conf.json`'s `beforeBuildCommand` is the staging script, which only
      copies `ui/app/dist`. Skip this and the installer ships whatever UI was last
      built, silently:
      ```powershell
      npm --prefix ui/app run build
      ```
      Staging now refuses a `dist` older than anything in `ui/app/src`, so a
      forgotten rebuild fails the build instead of shipping the old wizard.
- [ ] Bundle a real `uv.exe` — the staging script skips it unless told. Get your
      own path first; the one below is a placeholder, not a real location:
      ```powershell
      (Get-Command uv).Source
      ```
      Then run the build from a PowerShell prompt. Both statements go in one
      invocation, and do **not** put the word `powershell` in front of them — that
      spawns a nested shell which sets the variable and exits before `npm` ever
      sees it, and reports the confusing `The term '=' is not recognized`:
      ```powershell
      $env:BABY_UV_EXE = "<your path from above>"; npm --prefix ui/shell run build
      ```
      A path that does not exist now fails the build outright, so a typo cannot
      quietly ship an installer with no `uv.exe` in it.
- [ ] Confirm the payload actually contains it — staging prints `==> Included
      uv.exe` and a size in the tens of MB rather than ~3 MB:
      `ui/shell/src-tauri/payload/uv.exe`
- [ ] Installer lands at
      `ui/shell/src-tauri/target/release/bundle/nsis/Baby_6.0.0_x64-setup.exe`
      — check the **filename says 6.0.0**, not 5.0.0.
- [ ] Generate the checksum file published alongside it:
      ```powershell
      Get-FileHash .\Baby_6.0.0_x64-setup.exe -Algorithm SHA256 | Format-List
      ```

## 2. Clean-VM matrix

A fresh Windows 11 VM per row, snapshot before each. This is the acceptance test
for W3 and W5 — the dev box cannot fake any of it.

| # | Scenario | Expect |
|---|---|---|
| 1 | Fresh VM, **no VC++ runtime**, Full mode, 8 GB+ GPU | UAC prompt for the runtime, then a complete install; Baby answers a message |
| 2 | Same, but **decline the UAC prompt** | One legible error naming the runtime and how to install it — not a trace, not a silent hang |
| 3 | Fresh VM, **no NVIDIA GPU** | Wizard recommends cloud-only; Full is still selectable with a warning |
| 4 | **Non-admin account** | Whole install completes; only the VC++ step ever prompts |
| 5 | **Antivirus active** (Defender at minimum) | Installer runs; note anything quarantined |
| 6 | **Non-English locale** (e.g. German, Hindi) | No mojibake in the wizard; paths resolve |
| 7 | **Low disk** (< 8 GB free) | Refused *before* downloading, with the space needed |
| 8 | **Network drop mid `uv sync`** | Reopen Baby → resumes, does not restart from zero |
| 9 | **Network drop mid model pull** | Same — resumes from cached blobs |
| 10 | **Corporate proxy** (if reachable) | Message names the proxy and the env vars to set |

### What a machine already measured

Run on a VirtualBox guest (`baby-cleanvm`, Windows 11, snapshot per row, 8 GB
RAM, no GPU, `--audio-driver none`) against the **published**
`Baby_6.0.0_x64-setup.exe` — the same bytes on the Release page, not a local
build. This is evidence for the table above, **not a substitute for it**: none of
it ran on real hardware, and a VM cannot prove the things a VM does not have.

| # | Result | What was actually observed |
|---|---|---|
| 1 | pass, in part | UAC prompt, then a complete install. "Baby answers a message" is **not** covered — that needs a cloud key |
| 2 | pass | Declining UAC gives a named, legible error |
| 3 | pass | "No NVIDIA GPU detected"; cloud-only badged Recommended, Full still selectable with a warning |
| 4 | pass | Standard account `stduser`, absent from Administrators. `READY` at 13:52:37, `saw any UAC prompt: False` |
| 5 | pass | Defender on: 0 detections, 0 quarantined |
| 6 | pass, in part | See the note below — paths, not locale |
| 7 | pass | `disk error free_mb=9426 need_mb=13092`, refused **before** downloading |
| 8 | pass | Venv grew 1232.9 → 1308 MB across the drop; `UVCACHE_MB=1230` never moved, so the cache was reused rather than refetched |
| 9 | pass, plus two bugs | Resumed from cached blobs — and surfaced the two defects fixed in 6.0.1 (the `EventBus` `kind` collision, and a dead download that waited the full hour) |
| 10 | **inconclusive** | A dead local proxy returns connection-refused, which is indistinguishable from no network. Proving the proxy branch needs something that really answers **407** |

**Row 6 is a path test, not a locale test.** It ran under `C:\Users\Jörg`: the
installer exited 0, `BABY_HOME` built there (`.venv`, `config.yaml`, `logs`), and
SQLite opened and wrote its WAL at that path (`baby.db-shm`, `baby.db-wal`
present). `path_or_codec_errors = 0`, and the log bytes decode strict UTF-8 with
real `e28094` em dashes and no `c3a2e282ac` mojibake. The wizard renders "Full —
local + cloud" correctly. What this does **not** cover is a **translated Windows
UI** — a German or Hindi language pack was never installed, so row 6's own
wording is only half-satisfied. Do not let the path result stand in for it.

**Ignore one line in these guest logs:** `voice unavailable: PortAudioError:
Error querying device -1`. The VM was created with no audio device at all. It is
the harness, not a defect.

Still uncovered by any of the above, and still yours to run:

- Anything needing a real API key — row 1's "answers a message", every **Test**
  in §3, and the "not saved yet" warning, which only appears once a key has
  tested `ok`.
- Row 9's **Ollama blob** variant. The drop was measured against the Hugging Face
  downloads; the 9B pull needs a GPU box and ~5.5 GB.
- Everything in §6. A VM with no GPU and no microphone cannot speak to voice,
  the local 9B, or game mode.

## 3. First-run wizard

- [ ] GPU check reports the real card and VRAM.
- [ ] Mode fork: picking against the recommendation warns but is allowed.
- [ ] Provisioning shows per-item progress; closing and reopening **resumes**
      rather than restarting.
- [ ] **Bad API key** is rejected with "that key was rejected" — not a generic
      failure, and it is **not** written to `.env`.
- [ ] **Key validation against the LIVE vendors, both directions.** Every unit
      test here mocks the network, which is exactly how a probe that accepted any
      string shipped. In each of the three wizard rows, press **Test** with a
      deliberately mangled key and confirm it is **rejected**, then with a real
      key and confirm it **works**. Six results; any "works" on a mangled key
      means the probe is hitting an endpoint that does not authenticate.
- [ ] A `probe_unavailable` result means **Baby's own check model was retired**,
      not that the key is bad. If you see it, the probe model in `core/keys.py`
      (and possibly `nim_heavy` in `installer/config.default.yaml`) needs
      replacing with one NVIDIA still serves. `z-ai/glm-5.2` shipped dead once
      already.
- [ ] **Good API key** is accepted; `.env` exists in `%LOCALAPPDATA%\baby`.
- [ ] `icacls "%LOCALAPPDATA%\baby\.env"` shows a **single user grant**, with
      SYSTEM and Administrators absent.
- [ ] **Cloud-only with no key cannot finish** — Continue stays disabled.
- [ ] Disclosure step appears; Finish is disabled until the box is ticked, and it
      **names the wake phrase** ("Hey Jarvis") plus Ctrl+Alt+B.
- [ ] **With a cloud key: finishing restarts the backend by itself.** The wizard
      says "Baby is restarting itself", the window goes to an overlay for a few
      seconds, and comes back on the live UI. Then confirm it actually took:
      `/stats` carries a `router` and a `game_mode` key, and the cloud badge lights.
      Before this, the wizard stamped `cloud_primary` and the running process stayed
      local-only for its whole life with a valid key sitting unused in `.env`.
- [ ] **Without a cloud key (Full install): finishing still restarts if voice died
      at boot.** On a first run the wake-word models do not exist yet, so voice fails
      and the log says `Baby ready (text only)`. After the restart, confirm the log
      instead says `voice on (hey_jarvis)` and that **"Hey Jarvis" actually wakes it
      in that same session** — before this, a fresh install was deaf until the user
      happened to restart on their own.
- [ ] **Test a key, then press Continue.** The step must refuse to move on, and
      the field must say the key is not saved yet. Pressing only `Test` and
      continuing is how a real install finished setup with an empty `.env`, an
      unstamped `router_mode`, and a user who thought Baby was on the cloud --
      `Test` proves the key against the vendor and stores nothing.
- [ ] **Add a key AFTER setup, from the setup & repair panel.** It must accept the
      key (not just list it), and Baby must restart itself and come back on cloud.
      Before this the panel was read-only and told the user to hand-edit `.env` --
      which stamps no `router_mode`, so even a correct edit left Baby local-only.
- [ ] **Game mode on == GPU free, checked on the bar not the badge.** Right after
      the wizard restarts, the header must show game mode on AND the VRAM bar low.
      The wizard's verify step loads the 9B on purpose, so this is where a first
      run showed game mode on next to 8.0 of 9 GB. Confirm with Ollama itself:
      ```powershell
      (Invoke-RestMethod http://127.0.0.1:11434/api/ps).models
      ```
      Expect nothing resident. Then press "Run a check" in setup & repair and look
      again -- that probe loads the model too, and must give it back.
- [ ] **A second launch, with everything already provisioned, does NOT restart.** A
      bounce there is an outage for nothing.
- [ ] After finishing, **relaunch → the wizard does not reappear**.
- [ ] Search the whole of `%LOCALAPPDATA%\baby\logs` for your API key. Expect
      zero hits.
- [ ] **`baby.log` exists and its newest `--- baby start` line matches THIS
      launch.** An installed build wrote no log at all before this fix: the gate was
      `sys.stdout is None`, which uv's venv `pythonw.exe` never produces, so every
      crash was invisible and the only thing a user ever saw was a bare
      "Python-CFFI error" dialog. Check the timestamp, not just the file — a stale
      log from a dev run lives at the same path:
      ```powershell
      Select-String "--- baby start" "$env:LOCALAPPDATA\baby\logs\baby.log" | Select-Object -Last 1
      ```
- [ ] **The backend survives the whole first run.** It used to die partway through
      with `exit code -1073741819` (0xC0000005) — voice opened the mic, failed on
      wake-word models that were not downloaded yet, and left the stream running for
      the garbage collector to race. The crash landed minutes later during the
      embedder step, so watch the whole provisioning run, not just the start.
- [ ] **Rebuild the venv and Baby still hears you.** This is the case that broke:
      `ensure_venv` skips the bootstrap while `.venv\.baby-ready` stands, so a plain
      reinstall never re-syncs. Force the rebuild -- delete that sentinel (or
      reinstall after a data-deleting uninstall) -- then launch, confirm the
      readiness line says `voice on (hey_jarvis)` and that the phrase wakes it. Then
      check the files are where a venv rebuild cannot reach them:
      ```powershell
      (Get-ChildItem "$env:LOCALAPPDATA\baby\models\openwakeword\*.onnx").Count
      ```
      Expect 9. Before this, openWakeWord's weights lived inside the venv's
      site-packages, so the sync deleted them, and with setup already marked complete
      the wizard never re-ran — the install went permanently deaf with no message
      and no obvious way back.
- [ ] Every provisioning row ends as a tick, a dash, or a named error. A row still
      showing an empty circle on a finished install is a bug, not a slow step —
      "Ollama runtime" did exactly that whenever Ollama was already running, next to
      a ticked 9B.

## 4. Setup & repair panel

- [ ] 🛠 appears in the header (installed build only).
- [ ] "Run a check" reports honestly on a healthy install.
- [ ] Delete `models\kokoro-v1.0.onnx`, re-check → **kokoro is named** as broken.
- [ ] "Repair install" re-downloads it; the check goes green.
- [ ] Switch cloud-only → Full: the 9B downloads.
- [ ] "Create report" — confirm **no API key, no username, no owner name** in the
      output before you post it anywhere.

### Progress is legible while it runs

- [ ] During the first run, the **Wake-word models**, **Whisper** and **Memory
      embedder** rows count up ("downloading 8 of ~19 MB (2m)") rather than showing
      the bare word "working". These three have no Content-Length, so this text is
      the only progress they can show; the wake-word row reading "working" for six
      minutes is what got reported as a hung install.
- [ ] Pull the network mid-download and leave it: within ten minutes the row says
      "no new data for Nm" rather than continuing to look busy.
- [ ] Leave it pulled for another ten. The step must **give up** with a retryable
      error rather than sitting there until the one-hour ceiling. Restoring the
      network does NOT revive an interrupted download -- that was measured, with
      the machine pinging the host at 32 ms while the row stayed dead -- so the
      recovery is the error plus a reopen, and the row has to reach it promptly.

## 5. Uninstall

The W5 fix. Verify both branches.

> **Do not run the ticked branch on a machine that also runs Baby from source
> without backing up first.** `%LOCALAPPDATA%\baby` is not exclusive to an installed
> build: a dev checkout resolves its `logs`, `browser` profile, `shots` and file
> index there too, regardless of `BABY_HOME`. The uninstaller cannot tell the two
> apart, so ticking the box on your dev box takes those with it. The hook refuses
> when no install ever provisioned the directory (no `.venv` in it), which is not
> the same as being safe here — once you have installed, the venv is present and
> everything goes.
>
> Back up first, and restore afterwards:
> ```powershell
> robocopy "$env:LOCALAPPDATA\baby" "$env:USERPROFILE\baby-localappdata-backup" /E
> ```
> Prefer a clean VM for this section — that is what it is written for.

- [ ] Uninstall with **"Delete application data" ticked** → `%LOCALAPPDATA%\baby`
      is **gone** (keys, conversations, models, venv).
- [ ] Reinstall, set up, uninstall with the box **unticked** → the folder
      **survives**; reinstalling resumes with history intact.
- [ ] On a box where Baby was only ever run **from source** (no install), the
      uninstaller leaves `%LOCALAPPDATA%\baby` alone — the dev caches survive.
- [ ] **Upgrade path.** With a set-up install in place, double-click a newer
      `Baby_x.y.z_x64-setup.exe`, choose to uninstall the old version when offered,
      and **tick "Delete application data"** on the uninstaller's confirm page. The
      data **survives**: after the upgrade the wizard does not reappear, the saved
      key is still there, and `/api/diagnostics` still reports the conversation
      count it had before. This is the branch that ate a real user's keys and
      history twice — `$UpdateMode` is 0 on this path, so only the
      `$EXEDIR`/`$INSTDIR` guard stands between an upgrade and a wipe.
- [ ] Re-verify against the NSIS the release was built with: an uninstaller invoked
      by an installer runs in place, a standalone one copies itself to `%TEMP%`.
      `tests/test_uninstall.py::test_the_reinstall_guard_holds_against_real_nsis`
      does this automatically wherever `makensis.exe` is installed.

## 6. Regression on the real box

Baby is still the same assistant — confirm v6 packaging did not disturb it.

- [ ] Voice: wake word → transcribe → answer → speak. The phrase is **"Hey Jarvis"**
      on a public install — "Hey Baby" wakes nothing, and testing with it will make
      working voice look dead.
- [ ] Safety gate: a mutating command still asks first; a destructive one is refused.
- [ ] Local 9B answers offline (Full mode, network off).
- [ ] Cloud escalation and the brain badge behave as in v5.
- [ ] Memory, chat history, and search survive a restart.

## 7. Publish

- [ ] Merge the PR.
- [ ] Tag `v6.0.0`.
- [ ] Create the GitHub Release with the `.exe` **and** `SHA256SUMS.txt`.
- [ ] Release body links the SmartScreen walkthrough
      (`docs/INSTALL.md`) — a first-time user meeting an unexplained blue warning
      is the most likely reason a download gets abandoned.
