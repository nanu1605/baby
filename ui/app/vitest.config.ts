import { defineConfig } from "vitest/config";

// Pure-logic unit tests (B2): store reducers, graph layout math, markdown
// sanitize. jsdom is needed because DOMPurify runs against a DOM. No
// DOM/component tests — that scope stays out by design.
export default defineConfig({
  test: {
    environment: "jsdom",
    include: ["src/**/*.test.ts"],
  },
});
