// V0 shell spike — Electron preload. Bridges the shared harness's window.spikeAPI
// calls to the main process over IPC.
import { contextBridge, ipcRenderer } from "electron";

contextBridge.exposeInMainWorld("spikeAPI", {
  coldStartShellMs: () => ipcRenderer.invoke("spike:coldStartShellMs"),
  saveResult: (result: unknown) => ipcRenderer.invoke("spike:saveResult", result),
  saveScreenshot: () => ipcRenderer.invoke("spike:saveScreenshot"),
});
