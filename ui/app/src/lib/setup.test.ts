import { describe, expect, it } from "vitest";
import type { SetupGpu, SetupState } from "../types";
import {
  gpuSummaryLine,
  initialStep,
  isCounterRecommended,
  modeTradeoff,
  recommendedMode,
  shouldShowWizard,
} from "./setup";

const setup = (o: Partial<SetupState>): SetupState => ({
  complete: false,
  install_mode: null,
  installed: true,
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
  it("skips the mode fork when a mode was already recorded", () => {
    expect(initialStep("cloud_only")).toBe("done");
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
