import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import path from "node:path";

// Tauri dev drives this Vite server at a fixed port (matches tauri.conf.json
// build.devUrl). The shared scene is the @baby/spike-common workspace package;
// its realpath is spike/common (outside this root) so fs.allow must include it.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    strictPort: true,
    fs: { allow: [path.resolve(__dirname, ".."), __dirname] },
  },
  build: {
    outDir: "dist",
    emptyOutDir: true,
  },
});
