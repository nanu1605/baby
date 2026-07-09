import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import path from "node:path";

// Loaded via file:// by the Electron main process → base: "./". The shared scene
// is the @baby/spike-common workspace package (symlinked into node_modules); its
// realpath is spike/common (outside this root) so fs.allow must include it.
export default defineConfig({
  base: "./",
  plugins: [react()],
  server: {
    fs: { allow: [path.resolve(__dirname, ".."), __dirname] },
  },
  build: {
    outDir: "dist",
    emptyOutDir: true,
  },
});
