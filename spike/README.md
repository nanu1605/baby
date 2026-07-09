# V0 shell spike — Tauri vs Electron (owner-run on the 5060 Ti)

Throwaway comparison harness for **v4 phase V0**. It answers one question with real
numbers on the real GPU: **does WebView2 (Tauri) render bloom/postprocessing
acceptably, and which shell holds 60 fps within VRAM budget?** After you paste the
two results back, Claude tables them in `DECISIONS.md`, scaffolds the winner at
`ui/shell/`, and this whole `spike/` folder is deleted.

## What's identical vs what differs (the fair-comparison guarantee)

- **Byte-identical** (both shells import verbatim from [`common/`](common/)): the
  scene (`scene.tsx` — ~42 emissive nodes + ~50 glowing arcs + bloom), the idle
  spin, the **fixed time-based camera path**, the frame-time sampler, the VRAM
  poll, the 60 s window, and the result shape (`harness.ts`, `spikeApi.ts`,
  `nodes.ts`, `App.tsx`, `buttons.tsx`).
- **Differs** (the shell wrapper only): how `result.json` + the screenshot get
  written, and the shell-side cold-start timestamp — injected as
  `window.spikeAPI` by each shell (`electron/electron/*`, `tauri/src/spikeApiTauri.ts`
  + `tauri/src-tauri/*`).

So fps / VRAM / bloom are apples-to-apples: same pixels, same path, same window.

## Before you start

- **Start the backend** so the VRAM poll (`GET http://127.0.0.1:8765/stats`) has
  data: `uv run python run.py --ui` in another terminal. (Optional — if it's not
  up, VRAM comes from `nvidia-smi` per below; fps/bloom are unaffected.)
- **Record the VRAM baseline first:** with the backend up but **no spike running**,
  note `nvidia-smi` "Memory-Usage" (or the Baby header VRAM gauge). Call it
  `VRAM_baseline`. Each shell's reported `vram_used_gb` minus `VRAM_baseline` ≈
  **VRAM used by shell+scene** — the number the plan wants.
- Prereqs: Node LTS (already installed from v3). Tauri also needs the **Rust
  toolchain** (`rustup`, `winget install Rustlang.Rustup`) + WebView2 (ships on
  Win11).

## Install once (npm workspace root)

`spike/` is one npm workspace (`common` + `electron` + `tauri`), so the shared
scene installs a single hoisted copy — the fair-comparison guarantee. Install from
the spike root **once**:

```powershell
cd spike
npm install            # installs all three workspaces (downloads Electron too)
```

## Run — Electron

```powershell
cd spike\electron
npm run measure        # builds renderer + main, launches the window
```

- A 1280×800 window opens; the HUD top-left counts `0→60s` on a fixed camera path.
- At 60 s it writes **`spike\electron\result.json`** + **`spike\electron\screenshot.png`**
  (auto-captured — this is your bloom reference for Electron).
- Installer/packaged size (optional): `npm run dist` (unpacked → `release\win-unpacked`)
  or `npm run installer` (NSIS). Note the folder/exe size.

## Run — Tauri

```powershell
cd spike\tauri
npm run tauri icon src-tauri\icons\icon.png   # one-time: generates icon.ico etc. from the placeholder
npm run measure                                # = tauri dev; compiles Rust, launches the window
```

- First run compiles Rust (slow, one-time). Same HUD + 60 s window + fixed path.
- At 60 s it writes **`result.json`** (into the run dir — usually `spike\tauri\src-tauri\`;
  the path is printed to the console and the values are also on the HUD).
- **Bloom verdict (manual):** Tauri has no free in-process capture, so **eyeball the
  live window** and grab a manual shot (`Win+Shift+S`). The bloom question is a human
  judgment either way: does the glow look as good as Electron's, or washed-out /
  banded / missing?
- Installer/size (optional): `npm run installer` (NSIS bundle in
  `src-tauri\target\release\bundle\`). Note the `.exe`/setup size.

## What to paste back to Claude

For **each** shell:
1. The `result.json` contents (fps p50 / 1%-low / avg, cold-start, VRAM used).
2. `VRAM_used_by_shell = result.vram_used_gb − VRAM_baseline` (or the `nvidia-smi`
   delta if the poll got no samples).
3. **Bloom acceptable? yes / no** + one line (Electron's auto screenshot; Tauri your
   manual eyeball).
4. Installer/packaged size if you ran the optional build.

Claude then records the comparison table (`DECISIONS.md` #121), picks the winner,
scaffolds `ui/shell/`, and deletes `spike/`.

## Notes / honesty

- **fps is measured at vsync (uncapped).** The scene renders every rAF, so on a
  144 Hz panel expect ~140 fps, not 60. That's fine and fair: both shells hit the
  same vsync on the same monitor → apples-to-apples, and p50 well above 60 = headroom.
  What matters is **p50 vs 60** (headroom) and the **1%-low** (stutter), compared
  across the two shells.
- **Verified live by Claude on this box:** the **Tauri** spike runs end-to-end —
  window opens, WebView2 renders the bloom scene, the 60 s harness writes
  `result.json` (a quick test run: p50 ~143, 1%-low ~45, no backend so VRAM null).
  Both renderers also typecheck + Vite-build green and the Electron main/preload
  compile. **Not** verified here: Electron's live run (its `node_modules` needs the
  reboot fix), and the real **VRAM/bloom** numbers — start the backend + run it
  yourself for those.
- `webSecurity:false` (Electron) and `csp:null` (Tauri) are **spike-only** so the
  renderer can fetch `/stats` cross-origin. The shipped v4 shell will not do this.
- The scene is a fixed stand-in sized like the real brain graph — **not** the real
  `/api/graph`. The honest, signal-driven 3D sphere is V3. V0 only measures the
  rendering envelope.
