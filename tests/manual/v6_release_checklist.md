# v6.0.2 release checklist

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
      `ui/shell/src-tauri/target/release/bundle/nsis/Baby_6.0.2_x64-setup.exe`
      — check the **filename says 6.0.2**, not 6.0.1.
- [ ] Generate the checksum file published alongside it:
      ```powershell
      Get-FileHash .\Baby_6.0.2_x64-setup.exe -Algorithm SHA256 | Format-List
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

### Full mode on a machine that has never had Ollama

Until 6.0.1 this could not finish at all, and the dev box could not show it: Ollama
is already running there, so the whole branch is skipped.

- [x] **Pick Full on a clean machine with no Ollama.** The "Ollama runtime" row must
      install it by itself -- counting up ("installing Ollama (2m)"), not sitting on
      one word -- and reach a tick. No UAC prompt for this step: the install is
      per-user. Then the 9B pulls and "Verifying everything works" passes.
      Measured on a clean VM three times: by the harness against build `445F130D`
      (6 minutes, non-elevated), and by the owner against `260168DB` and then
      `1F08096B`, the binary that ships. Working as expected each time.
- [ ] **Confirm the same on real hardware.** Both runs above were VMs. A real box
      differs in the ways that matter here: a GPU, a working audio device, and
      whatever is already installed and competing for the port.
- [ ] **Confirm the context length actually got set**, or the local brain silently
      serves a truncated context and everything just feels stupider:
      ```powershell
      [Environment]::GetEnvironmentVariable("OLLAMA_CONTEXT_LENGTH", "User")
      ```
      Expect `8192`.
- [ ] **The escape hatch — the one thing not machine-verified.** Make Full fail on
      the local brain ALONE (easiest: let setup finish, then exit Ollama from its
      tray icon, delete `%LOCALAPPDATA%\baby\setup.json`, and relaunch so
      provisioning re-runs with everything else already cached). The error must be a
      sentence naming ollama.com -- **not** `ConnectError: [WinError 10061]` -- must
      say it once rather than twice, and must offer **"Use cloud only instead"**
      beside Retry. Click it: the plan reloads without the Ollama rows and setup
      completes. Cutting the network does NOT reproduce this — the embedder fails
      first and the offer is correctly withheld.
- [ ] **The offer must NOT appear when something shared broke.** Break whisper
      (delete its cache with the network off) and confirm only Retry is offered:
      cloud-only needs whisper too, so switching modes would fix nothing.

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
- [ ] **Read the error it gives up with.** It must tell you to close and reopen
      Baby. Retrying without reopening was measured doing nothing at all -- another
      20 minutes on an 11 ms link, byte count frozen -- so a message pointing at
      Retry is pointing at the one action that cannot work.

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
- [ ] **Two things a full uninstall deliberately leaves behind.** Neither is a bug;
      confirm they are still what is expected rather than assuming.
      ```powershell
      [Environment]::GetEnvironmentVariable("OLLAMA_CONTEXT_LENGTH", "User")  # 8192
      winget list --id Ollama.Ollama
      ```
      Ollama itself stays installed -- Baby installs it but does not own it, and
      removing another app on the way out would be worse than leaving it -- and the
      context-length variable stays with it, because it configures the daemon the
      user still has. See DECISIONS #154. Both are removed by uninstalling Ollama
      from Add/Remove Programs and clearing the variable by hand.

### What 6.0.2 changed, and what only a person can confirm

These were reported from a real desktop, which is the point: a VM matrix cannot see
most of them, because they are about *using* Baby rather than installing it. The one
exception is the upgrade check below, and it is the most important row on this page.
The automated side is green; these are the parts a machine in this repo cannot prove.

- [ ] **The "Get a key" links open a browser.** In the wizard and again in Setup &
      repair, click each of the three. A real browser window must open on the
      provider's page. Verified in a dev browser that the click no longer navigates
      the app and that the request fires; what is NOT verified is the browser
      actually opening from a windowless backend process — `webbrowser.open` shells
      out through Windows, and the installed backend runs with no console.
- [ ] **Links in Baby's own replies still do nothing.** Ask Baby something that
      makes it cite a URL and click it: expect nothing to happen. This is the
      known, deliberate gap (DECISIONS #156) — confirm it is still only *that*,
      and that clicking does not navigate the app away from the UI.
- [ ] **Open Setup & repair.** The 🛠 in the top bar. The dialog must appear and the
      app must stay on screen — a candidate shipped where this blanked the whole
      window, on every route in, and nothing automated saw it. **"Start with
      Windows" must be the first section**, visible without scrolling. Close and
      reopen it twice: still fine.
- [ ] **The search box must not touch the chat panel.** Press the omnibox open at the
      default window size and look at its right edge against the Chat/Activity tabs.
      Then collapse the chat panel, then the chat list, then both: it must re-centre
      on the space that is left each time, never overlap either edge, and never leave
      the window with a horizontal scrollbar.
- [ ] **Resize the window from wide to narrow.** Drag it from full width down to
      roughly 900px. The top bar must lose its gauges and wordmark rather than
      clipping or growing a scrollbar, and **Stop**, the icon buttons and the UI
      switch must survive to the narrowest size. Open the omnibox and the side
      panel at a few widths: neither may tuck under the bar.
- [ ] **The strip above the header.** The reporter's screenshots show scrambled
      text in the title-bar row, in both UIs. It does not reproduce in a browser
      tab, so it is a shell-level thing and is unfixed. Confirm whether it is still
      there on this build, and whether it survives a window resize or a tray
      "Reload UI" — that is the difference between a paint artifact and a bug.
- [ ] **The UI round trip.** Click `classic UI`, then `new UI` in the classic
      header, then back again. No restart at any point. Verified against a live
      backend by navigation; clicking a link could not be exercised in the embedded
      test browser, where even the untouched `classic UI` link does not navigate.
- [ ] **Start with Windows — the whole cycle.** In Setup & repair, turn it on.
      Confirm the value exists, then reboot:
      ```powershell
      Get-ItemProperty "HKCU:\Software\Microsoft\Windows\CurrentVersion\Run" -Name Baby
      ```
      Expect `"…\Baby.exe" --minimized`. After the reboot Baby must be **in the
      tray with no window**, and **nothing may flash on screen** during the logon —
      watch for it, that is the one thing this pass exists to catch. The tray starts
      amber ("Baby - starting…") and goes green only once the backend is really up.
      Click the tray icon: the window opens on a working UI, not a splash.
- [ ] **A logon start that fails must say so.** With autostart on, rename
      `%LOCALAPPDATA%\baby\.venv` and reboot. The tray must go **red**, reading
      "Baby - not running. Click to see why." — not green, and not amber forever.
      Click it: the window opens carrying the reason. Then, with the backend still
      down, **launch Baby from its shortcut**: a window must appear. (Before this
      release, both of those showed nothing at all.) Rename the venv back.
- [ ] **The off switch survives an attached backend.** Start `run.py --all` by hand,
      then open Baby's window: the "Start with Windows" section must still be there,
      showing the current state, with the off switch usable. Only *turning it on* may
      be unavailable in that configuration, and the panel must say why.
- [ ] **Turn it off and reboot again.** Baby must not start. Then turn it on once
      more and **uninstall** with autostart still enabled: the Run value must be
      gone afterwards, whether or not you ticked "delete application data".
- [ ] **A failed step says why.** In Setup & repair, break something (rename
      `models\kokoro-v1.0.onnx`) and run Repair. The row must read a sentence, not
      the bare word `error`.
- [ ] **Nothing in Baby broke when other sites stopped being able to reach it.**
      This is the one change nobody asked for, so it gets checked as a regression
      rather than a feature: send a message, run a tool, switch install mode, save
      a key, start a repair, and **watch the tray change colour** while a tool runs.
      The tray is the sharp case — it connects over a WebSocket from the Rust shell,
      not the browser, and a colour stuck on one value means its handshake is being
      refused. Then confirm the block actually works: open any page in an ordinary
      browser (a blank tab on some website, not a `file://` page) and run
      ```js
      new WebSocket("ws://127.0.0.1:8765/ws/chat").onerror = () => console.log("refused")
      ```
      It must log `refused`. From Baby's own window that same line connects.

### Startup, asked and applied (6.0.2)

The installer now asks. Everything below needs a real machine: the prompt, the
registry write, and a logon.

- [ ] **A fresh install asks.** On a machine with no Baby, run the installer. After
      the files copy, it must ask whether to start Baby when you sign in. Answer
      **Yes**, then check
      `HKCU\Software\Microsoft\Windows\CurrentVersion\Run` holds a `Baby` value equal
      to `"<install dir>\baby-shell.exe" --minimized` -- quotes included.
- [ ] **Answering No writes nothing.** Same again on a clean machine, answer **No**,
      and confirm the `Baby` value is absent.
- [ ] **It matches the in-app toggle.** After answering Yes, open Setup & repair: the
      "Start with Windows" section must already read as on. Turn it off there and
      confirm the Run value disappears. These two must never disagree.
- [ ] **An upgrade does not re-ask and does not undo it.** With autostart on, install
      over the top. It must NOT ask again, and the Run value must survive unchanged --
      including still pointing at the right exe.
- [ ] **A silent install gets nothing.** `Baby_6.0.2_x64-setup.exe /S`. No prompt, and
      no `Baby` Run value afterwards.
- [ ] **The logon cycle.** Reboot with it on: Baby comes up in the tray, no window
      flashes, the tray icon opens it, and the backend answers. Break the venv and
      reboot: the tray goes red rather than failing silently. Turn it off, reboot,
      confirm it does not start. Uninstall with it on and confirm the Run value is
      gone.

### The version is visible (6.0.2)

- [ ] **Setup & repair opens with the version.** It must read the version you just
      installed. If it reads an older one, the install did not fully apply -- capture
      `HKCU\...\Uninstall\Baby` `DisplayVersion`, `payload\pyproject.toml`, and
      `baby-shell.exe`'s date before doing anything else.
- [ ] **No false alarm in a source checkout.** Run from the repo: the shell version is
      absent there and must read as unknown, with no half-applied warning.
- [ ] **The installer refuses to lie.** If an install ever ends with the old version
      still on disk, it must have shown the "did not install" dialog and landed on its
      failure page rather than saying Completed.

> **A warning about diagnosing this from a Claude Code session on this machine.**
> The desktop app is an MSIX package, so a session's view of `%LOCALAPPDATA%\baby` is
> a redirected copy-on-write overlay: some files read back as stale copies and some
> fall through to the real ones, and a directory listing cannot tell which. During
> 6.0.2 that produced five agreeing measurements and one confident, wrong conclusion
> that the installer had silently failed. **Only the live process is authoritative** --
> `/stats`, the backend's own environment block, or a marker planted in a file to see
> whether the server returns it. See DECISIONS #164.

### The chat list keeps up (6.0.2)

Reported: chats appeared only when "Show archived" was ticked. Verified against the
built bundle on a stub backend; these rows are the same checks against a real one.

- [ ] **A reply updates the list without touching the filter.** With "Show archived"
      unticked, send a message. The row's timestamp and message count must update on
      their own.
- [ ] **A new chat appears on its own.** Click "+ New chat", send one message. The new
      row must appear, titled from that message, highlighted as active -- with the
      checkbox still unticked.
- [ ] **Clicking a chat opens it.** Click a past chat: the transcript loads and the
      composer is there, ready to type. Not a read-only banner.
- [ ] **Clicking mid-answer opens it read-only.** Ask Baby something slow, and while
      it is answering click a different chat. It must open read-only with a toast
      explaining why -- not do nothing, and not switch the conversation underneath the
      running reply.
- [ ] **Game mode still names its conversation.** Type `game mode on`. The list must
      not lose track of which chat is active -- that path writes its own `turn_start`
      frame and is the only turn in the app that never reaches the bus.

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

Another **patch on a release that is already public**. v6.0.1 is downloadable now,
so anyone installing before this ships meets all five bugs below; that is the
argument for its own tag rather than waiting to be batched.

> **What v6.0.1 already proved, for reference.** Row 9 was re-run against that
> build and passed: cut at 68 MB of 1.6 GB, the row said "no new data for 10m" at
> +10.1m and gave up at +19.8m with `kind=stalled`, `retryable=true`, and a log
> scan for `TypeError` / `Traceback` returned 0 hits. Its Ollama fix was proven on
> three separate clean-VM runs, one per candidate binary, and the shipped
> `1F08096B` was installed and run by the owner. None of that transfers to 6.0.2:
> Phase 5 changed the provisioning failure path again.

- [ ] **Re-run matrix row 9 against the 6.0.2 build.** The hub steps now retry
      once against the cache when the network fails, and the `provision` row is
      classified rather than raw — both live exactly where row 9 pulls the plug.
      Cutting the network mid-download must still give up with a retryable error,
      and the row must still name the reopen.
- [ ] **A first run of the candidate binary itself.** A rebuild is a different file
      even when the source is identical, so each candidate gets its own run rather
      than inheriting the last one's. Record the size and SHA256 here.

      Candidate, built from a clean tree at `51169e7`:
      ```
      Baby_6.0.2_x64-setup.exe   19,068,522 bytes
      68447A3E162413A0EFDB6ED64124787E84935E582FB540B765B8CDC1E2D84B4E
      ```
      First candidate whose installer **asks** about starting with Windows, which is
      what was reported twice; the earlier ones only had the toggle in Setup & repair.

      Supersedes `045D204B…` (clean at `ba55c75`), `7FE4B724…` (at `88c588d`) and
      `3461DB69…` (at `a43a226`, the RepairPanel crash). **Every candidate before this
      one installs correctly** — the claim that `7FE4B724…` was run over a 6.0.0
      install and replaced nothing was my own misreading of an MSIX-redirected
      filesystem, corrected in DECISIONS #164. Do not go looking for that bug.

      Verified as an artifact rather than as source, as every candidate since
      `3461DB69…` has been: a production React bundle is a different artifact whose
      invariants only fire when you run it. For the SPA that meant driving the built
      `dist` against a scripted stub backend — the version line quiet when the two
      versions agree, carrying the warning when they disagree, quiet when the shell
      version is absent; a new chat appearing in the list on its own with "Show
      archived" untouched; clicking a chat firing `resume` first and leaving a live
      composer; a refused resume falling back to the viewer with one toast. For the
      installer hook it meant compiling it with real NSIS and running the guards
      against a scratch registry key.

      **The prompt's syntax broke the build once** — NSIS takes `/SD` after the
      message text, and the lifted-block tests replace that line to model an answer,
      so nothing compiled the prompt itself. `test_the_whole_hook_compiles` now
      compiles the shipped hooks file, both macros, and is the cheapest gate here.

      Verified before it left this machine: 72 payload `.py` files, 0 content
      differences against HEAD (43 differ in line endings only, which is
      `core.autocrlf` and not a change); the SPA `dist` identical to the one just
      built; all seven fixes present in the payload; 0 secret-shaped files; no
      `tests/`; `uv.exe` bundled. That last one is not decoration — the first
      attempt at this build ran the plain `npm run build`, which stages **no**
      `uv.exe`, and a fresh install of it would have stopped at "First-run setup
      files are missing." Step 33 above exists for exactly that and was skipped.
- [ ] Merge the PR.
- [ ] Tag `v6.0.2`.
- [ ] Create the GitHub Release with the `.exe` **and** `SHA256SUMS.txt`.
- [ ] Leave v6.0.0 and v6.0.1 up. Their `.exe`s and checksums stay valid for
      anyone who already has them, and deleting a published asset breaks a hash
      someone may have written down.
- [ ] Release body links the SmartScreen walkthrough
      (`docs/INSTALL.md`) — a first-time user meeting an unexplained blue warning
      is the most likely reason a download gets abandoned.
