# v6 W0 packaging spike

Throwaway proofs that de-risk the v6 public-installer unknowns before any real
installer code (same discipline as v4's Tauri-vs-Electron spike). Results feed
`DECISIONS.md` #127. **This directory is deleted in W1** once the decisions are
ratified and the real first-run code begins — nothing here is shipped.

Everything a normal dev box can prove has been run and is green. The two proofs
that genuinely need a **clean, no-Python Windows VM** (owner-run) are called out.

---

## 1. Backend delivery — bundled `uv` + functional wheel probe + failure UX

**The crux.** The installer bundles a tiny `uv.exe` + pinned `pyproject.toml` +
`uv.lock`; first-run stands up `%LOCALAPPDATA%\baby\.venv` from the lock. A green
`uv sync` is NOT proof — a native wheel can install yet fail to load.

- `backend_delivery/health_probe.py` — post-sync **functional** probe: imports each
  native wheel and does a trivial real op (loads the .pyd, so a broken dlopen /
  missing VC++ redist surfaces HERE, not mid-conversation). Structured `--json`
  output; exit 0 only if every required wheel functions. **This ports into W3's
  health check verbatim.**
- `backend_delivery/first_run.ps1` — the bundled-uv flow: `uv python install` →
  `uv sync` (into the data-dir venv, retried with backoff) → probe → optional
  `run.py --all` launch. Includes `Resolve-SyncError`, which reframes a `uv sync`
  failure as a legible, retryable message (never a raw trace).

**Proven on the dev box (2026-07-11):**
- Probe: all 10 required wheels + 3 optional PASS — `torch 2.12.1+cpu`,
  `ctranslate2 4.8.1`, `faster_whisper 1.2.1`, `onnxruntime 1.27.0`,
  `sqlite-vec v0.1.9` (extension actually loaded, `vec_version()` returned),
  `sherpa_onnx 1.13.3`, kokoro/openwakeword/silero import, **live Chromium launch**,
  NVML (RTX 5060 Ti, 8.0 GB), 23 audio devices.
- `uv sync --frozen` resolves the pinned lock (145 packages, audit, no re-download).
- Failure UX verified against **real** `uv` stderr: a forced DNS failure classified
  as NO-INTERNET (not PROXY — the naive `CONNECT` match was a bug, caught + fixed:
  uv's `client error (Connect)` chain must not be read as a proxy problem); a
  synthetic 407 → PROXY; disk-full → DISK.

**Owner, clean no-Python VM:** run `first_run.ps1 -UvExe .\uv.exe -SourceDir <payload>`
with NO Python/uv preinstalled — confirm the managed CPython installs, `uv sync`
builds the venv, and the probe is all-green. This is the real end-to-end proof the
dev box (which already has the wheels) can only approximate.

## 2. Model delivery — Ollama pull progress + resume

`model_pull/pull_progress.py` streams `POST /api/pull` and renders bytes / % /
speed / ETA from the `{status,digest,total,completed}` objects. The pull is
resumable by construction (content-addressed blobs).

**Proven on the dev box:** a real `all-minilm` pull rendered live byte progress; a
second pull hit the cached path (manifest → verifying → success, no re-download),
proving re-issue resumes; spike model removed afterward. The exact JSON shape here
is what W3's progress component consumes.

**Owner, clean VM:** start the 9B (`qwen3.5:9b-q4_K_M`) pull, kill the NIC mid-pull,
re-run — confirm it resumes and finishes.

## 3. Installer engine — NSIS (Tauri v2.1.0, already the bundle target)

No new engine needed. `tauri-cli 2.1.0` + the existing `bundle.targets:["nsis"]`
already produced `Baby_<ver>_x64-setup.exe` (~1.6 MB, unsigned) on this box — the
toolchain works and the tiny size confirms the web-installer premise (installer
ships almost nothing; first-run fetches the weight).

Tauri's NSIS bundler covers the wizard needs: a **license/EULA page**, **per-user
`installMode: currentUser`** (no admin), **post-install hooks** to trigger first-run,
and a native uninstaller (+ header/sidebar/installer-icon art). Exact `tauri.conf`
keys are wired + verified against the installed CLI in W1.

**Finding:** NSIS has **no MSI-style Repair/Modify ARP dialog**. So v6 does **repair
and mode-switch (cloud-only ↔ full) IN-APP** (W5 settings / health-check), and
leaves clean uninstall to the native NSIS uninstaller. This matches spec §9's intent
(repair/modify as an in-app action) and avoids a WiX/MSI detour.

## 4. Signing — unsigned now, hook wired, SignPath-OSS as the free track

Owner ships **free** → the build stays **unsigned** for now (Tauri signing config
omitted) + a documented SmartScreen "More info → Run anyway" walkthrough +
`.exe` checksums. The **hook** is a single-key drop-in later: set
`bundle.windows.signCommand` (or `certificateThumbprint` + `timestampUrl`) — no other
change. The only genuinely-free *trusted*-signature path for a public OSS repo is
**SignPath.io Foundation (free OSS signing)**; enrollment is a parallel owner track,
not a blocker.

---

## Install-time vs first-run boundary (finalized)

| Ships in the `.exe` (small) | Fetched on first run (network) |
|---|---|
| Tauri shell + wizard UI | managed CPython (`uv python install`) |
| bundled `uv.exe` | `uv sync` deps ~1–1.5 GB (CPU torch, ctranslate2, onnxruntime, …) |
| pinned `pyproject.toml` + `uv.lock` | **[Full]** Ollama (silent) + `qwen3.5:9b-q4_K_M` ~6.6 GB |
| conservative `config.default.yaml` | voice/memory assets: whisper ~1.6 GB, Kokoro, wespeaker, openWakeWord, e5 ~470 MB |
| `EULA.txt` | Playwright Chromium (optional) |

**Offline-first-install is out of scope** — the web-installer requires first-run
network by design (`uv sync` pulls wheels from PyPI; models pull from the Ollama
registry). Recorded in DECISIONS #127.
