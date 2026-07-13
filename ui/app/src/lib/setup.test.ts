import { describe, expect, it } from "vitest";
import type { SetupGpu, SetupState, SetupStatus } from "../types";
import {
  firstError,
  formatSize,
  gpuSummaryLine,
  initialStep,
  isCounterRecommended,
  modeTradeoff,
  provisionOutcome,
  recommendedMode,
  rowBar,
  rowStatus,
  shouldShowWizard,
  stepGlyph,
} from "./setup";

const setup = (o: Partial<SetupState>): SetupState => ({
  complete: false,
  install_mode: null,
  installed: true,
  provisioned: false,
  ...o,
});

const gpu = (o: Partial<SetupGpu>): SetupGpu => ({
  has_nvidia: true,
  gpu_name: "RTX 4070",
  vram_total_gb: 12,
  meets_full_bar: true,
  recommend: "full",
  full_bar_gb: 8,
  ...o,
});

describe("shouldShowWizard", () => {
  it("shows in an installed build that isn't set up", () => {
    expect(shouldShowWizard(setup({}), false)).toBe(true);
  });
  it("never shows in a dev checkout (installed:false), even when incomplete", () => {
    expect(shouldShowWizard(setup({ installed: false }), false)).toBe(false);
  });
  it("hides once setup is complete", () => {
    expect(shouldShowWizard(setup({ complete: true }), false)).toBe(false);
  });
  it("hides when dismissed this session", () => {
    expect(shouldShowWizard(setup({}), true)).toBe(false);
  });
  it("hides before the first /stats resolves (undefined)", () => {
    expect(shouldShowWizard(undefined, false)).toBe(false);
  });
});

describe("initialStep", () => {
  it("opens on the mode fork when no mode chosen yet", () => {
    expect(initialStep(null)).toBe("mode");
    expect(initialStep(undefined)).toBe("mode");
  });
  it("resumes provisioning when a mode is chosen but deps aren't installed", () => {
    expect(initialStep("cloud_only")).toBe("provision");
    expect(initialStep("cloud_only", false)).toBe("provision");
  });
  it("goes straight to done once provisioned", () => {
    expect(initialStep("cloud_only", true)).toBe("done");
  });
});

describe("gpuSummaryLine", () => {
  it("names the card and VRAM when present", () => {
    expect(gpuSummaryLine(gpu({}))).toBe("RTX 4070 · 12.0 GB VRAM");
  });
  it("reads 'no NVIDIA GPU' when absent", () => {
    expect(gpuSummaryLine(gpu({ has_nvidia: false, vram_total_gb: null }))).toBe(
      "No NVIDIA GPU detected",
    );
  });
});

describe("recommendedMode / isCounterRecommended", () => {
  it("recommends full above the bar", () => {
    const g = gpu({});
    expect(recommendedMode(g)).toBe("full");
    expect(isCounterRecommended(g, "full")).toBe(false);
    expect(isCounterRecommended(g, "cloud_only")).toBe(true); // capable GPU, cloud pick
  });
  it("recommends cloud-only below the bar / no GPU", () => {
    const g = gpu({ vram_total_gb: 4, meets_full_bar: false, recommend: "cloud_only" });
    expect(recommendedMode(g)).toBe("cloud_only");
    expect(isCounterRecommended(g, "full")).toBe(true); // weak GPU, forced Full
    expect(isCounterRecommended(g, "cloud_only")).toBe(false);
  });
});

describe("modeTradeoff", () => {
  it("distinguishes the two modes", () => {
    expect(modeTradeoff("full")).toMatch(/offline/i);
    expect(modeTradeoff("cloud_only")).toMatch(/cloud only/i);
  });
});

// -- W3 provisioning helpers -------------------------------------------------

const status = (o: Partial<SetupStatus>): SetupStatus => ({
  provisioning: false,
  progress: {},
  ...o,
});

describe("formatSize", () => {
  it("shows GB above 1024 MB, MB below, nothing for 0", () => {
    expect(formatSize(1600)).toBe("1.6 GB");
    expect(formatSize(310)).toBe("310 MB");
    expect(formatSize(0)).toBe("");
  });
});

describe("provisionOutcome", () => {
  it("is idle with no status or an empty snapshot", () => {
    expect(provisionOutcome(null)).toBe("idle");
    expect(provisionOutcome(status({}))).toBe("idle");
  });
  it("is running while the flag is set", () => {
    expect(provisionOutcome(status({ provisioning: true }))).toBe("running");
  });
  it("is done only when verify passed", () => {
    const s = status({ progress: { verify: { dep: "verify", phase: "verify", status: "pass" } } });
    expect(provisionOutcome(s)).toBe("done");
  });
  it("is error when any step failed (but not for needs_install)", () => {
    const bad = status({ progress: { kokoro: { dep: "kokoro", phase: "error", status: "error" } } });
    expect(provisionOutcome(bad)).toBe("error");
    const soft = status({
      progress: { vcredist: { dep: "vcredist", phase: "check", status: "needs_install" } },
    });
    expect(provisionOutcome(soft)).not.toBe("error");
  });
});

describe("row helpers", () => {
  it("rowStatus falls back to pending until an event lands", () => {
    expect(rowStatus("kokoro", {})).toBe("pending");
    expect(rowStatus("kokoro", { kokoro: { dep: "kokoro", phase: "download", status: "done" } })).toBe(
      "done",
    );
  });
  it("rowBar only renders for an active download with a pct", () => {
    expect(rowBar(undefined)).toBeNull();
    expect(rowBar({ dep: "k", phase: "download", status: "done" })).toBeNull();
    expect(
      rowBar({ dep: "k", phase: "download", status: "working", pct: 42, human: "42MB/100MB" }),
    ).toEqual({ pct: 42, label: "42MB/100MB" });
  });
  it("stepGlyph maps each status class", () => {
    expect(stepGlyph("done")).toBe("✓");
    expect(stepGlyph("error")).toBe("✕");
    expect(stepGlyph("working")).toBe("↓");
    expect(stepGlyph("pending")).toBe("○");
  });
});

describe("firstError", () => {
  it("returns the first failing step's message, else empty", () => {
    expect(firstError({})).toBe("");
    expect(
      firstError({ kokoro: { dep: "kokoro", phase: "error", status: "error", message: "net down" } }),
    ).toBe("net down");
  });
});
