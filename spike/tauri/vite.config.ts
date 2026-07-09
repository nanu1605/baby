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
    // Tauri writes Rust build output under src-tauri/target; if Vite watches it,
    // cargo locking build_script_build.exe mid-compile throws EBUSY. Never watch it.
    watch: { ignored: ["**/src-tauri/**"] },
  },
  // Keep cargo's compile output visible under `tauri dev`.
  clearScreen: false,
  build: {
    outDir: "dist",
    emptyOutDir: true,
  },
});
