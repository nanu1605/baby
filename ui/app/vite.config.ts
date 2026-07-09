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
  build: {
    outDir: "dist",
    sourcemap: false,
    emptyOutDir: true,
    rollupOptions: {
      output: {
        // Isolate the three.js/R3F stack into its own chunk so it is lazy-loaded
        // with BrainSphere and never bloats the entry bundle for 2D users (V3a).
        manualChunks(id) {
          if (
            id.includes("node_modules/three") ||
            id.includes("node_modules/@react-three") ||
            id.includes("node_modules/postprocessing")
          ) {
            return "three";
          }
        },
      },
    },
  },
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
