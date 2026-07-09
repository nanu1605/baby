// V0 shell spike — Electron main process. Thin shell glue only; the scene and
// measurement are byte-identical shared code in ../../common. Writes result.json
// + screenshot.png into the launch dir (spike/electron, gitignored).

import { app, BrowserWindow, ipcMain } from "electron";
import * as fs from "node:fs/promises";
import * as path from "node:path";

const PROCESS_START = Date.now(); // ~process spawn
let shownAt = 0;
let win: BrowserWindow | null = null;

function createWindow(): void {
  win = new BrowserWindow({
    width: 1280,
    height: 800,
    show: false,
    backgroundColor: "#0b0d12",
    webPreferences: {
      preload: path.join(__dirname, "preload.js"),
      contextIsolation: true,
      nodeIntegration: false,
      // Spike-only: allow the renderer to fetch http://127.0.0.1:8765/stats
      // cross-origin for VRAM. Never do this in the shipped shell.
      webSecurity: false,
    },
  });

  win.once("ready-to-show", () => {
    shownAt = Date.now();
    win?.show();
  });

  void win.loadFile(path.join(__dirname, "..", "dist", "index.html"));
}

ipcMain.handle("spike:coldStartShellMs", () => (shownAt ? shownAt - PROCESS_START : null));

ipcMain.handle("spike:saveResult", async (_e, result: unknown) => {
  const out = path.resolve(process.cwd(), "result.json");
  await fs.writeFile(out, JSON.stringify(result, null, 2), "utf-8");
});

ipcMain.handle("spike:saveScreenshot", async () => {
  if (!win) return;
  const img = await win.webContents.capturePage();
  const out = path.resolve(process.cwd(), "screenshot.png");
  await fs.writeFile(out, img.toPNG());
});

app.whenReady().then(createWindow);
app.on("window-all-closed", () => app.quit());
