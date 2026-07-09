import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Built `dist/` is served by FastAPI at `/` (production needs no Node).
// In dev, `npm run dev` proxies API + WS calls to the live backend on 8765
// so the Vite HMR shell talks to the real Baby — see scripts/dev_ui.ps1.
const backend = "http://127.0.0.1:8765";
const wsBackend = "ws://127.0.0.1:8765";

export default defineConfig({
  plugins: [react()],
  base: "/",
  build: { outDir: "dist", sourcemap: false, emptyOutDir: true },
  server: {
    port: 5173,
    strictPort: true,
    proxy: {
      "/stats": backend,
      "/history": backend,
      "/tasks": backend,
      "/projects": backend,
      "/memory": backend,
      "/confirm": backend,
      "/kill": backend,
      "/game_mode": backend,
      "/conversation": backend,
      "/api": backend,
      "/ws": { target: wsBackend, ws: true },
    },
  },
});
