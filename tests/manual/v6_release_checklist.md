# v6.0.0 release checklist

Everything here is **owner-run**. The dev box cannot validate a stranger's first
launch, and nothing in this file is something Claude does: the merge, the tag, and
the publish are yours.

Automated gates (`pytest`, `vitest`, `ruff`, `cargo check`, a real `tauri build`)
are already green on the branch — this covers only what a machine in this repo
cannot prove.

---

## 0. Blockers before any of this matters

- [ ] **`LICENSE` file exists.** The repo is currently all-rights-reserved, which
      makes a public release meaningless and blocks SignPath's free OSS signing
      (it requires an OSI-approved license — MIT or Apache-2.0). This is a
      decision only you can make. **Everything below assumes it is done.**
- [ ] Back up `baby.db` and your local `config.yaml` before installing any build
      on your own machine.

## 1. Build the artifact

- [ ] Bundle a real `uv.exe` — the staging script skips it unless told:
      ```powershell
      $env:BABY_UV_EXE = "C:\path\to\uv.exe"; npm --prefix ui/shell run build
      ```
- [ ] Confirm the payload actually contains it (staging prints a warning if not):
      `ui/shell/src-tauri/target/release/payload/uv.exe`
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

## 3. First-run wizard

- [ ] GPU check reports the real card and VRAM.
- [ ] Mode fork: picking against the recommendation warns but is allowed.
- [ ] Provisioning shows per-item progress; closing and reopening **resumes**
      rather than restarting.
- [ ] **Bad API key** is rejected with "that key was rejected" — not a generic
      failure, and it is **not** written to `.env`.
- [ ] **Good API key** is accepted; `.env` exists in `%LOCALAPPDATA%\baby`.
- [ ] `icacls "%LOCALAPPDATA%\baby\.env"` shows a **single user grant**, with
      SYSTEM and Administrators absent.
- [ ] **Cloud-only with no key cannot finish** — Continue stays disabled.
- [ ] Disclosure step appears; Finish is disabled until the box is ticked.
- [ ] After finishing, **relaunch → the wizard does not reappear**.
- [ ] Search the whole of `%LOCALAPPDATA%\baby\logs` for your API key. Expect
      zero hits.

## 4. Setup & repair panel

- [ ] 🛠 appears in the header (installed build only).
- [ ] "Run a check" reports honestly on a healthy install.
- [ ] Delete `models\kokoro-v1.0.onnx`, re-check → **kokoro is named** as broken.
- [ ] "Repair install" re-downloads it; the check goes green.
- [ ] Switch cloud-only → Full: the 9B downloads.
- [ ] "Create report" — confirm **no API key, no username, no owner name** in the
      output before you post it anywhere.

## 5. Uninstall

The W5 fix. Verify both branches.

- [ ] Uninstall with **"Delete application data" ticked** → `%LOCALAPPDATA%\baby`
      is **gone** (keys, conversations, models, venv).
- [ ] Reinstall, set up, uninstall with the box **unticked** → the folder
      **survives**; reinstalling resumes with history intact.

## 6. Regression on the real box

Baby is still the same assistant — confirm v6 packaging did not disturb it.

- [ ] Voice: wake word → transcribe → answer → speak.
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
