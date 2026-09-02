import { describe, expect, it } from "vitest";
import type {
  SetupComplete,
  SetupDisclosure,
  SetupGpu,
  SetupKeyResult,
  SetupKeyRow,
  SetupKeys,
  SetupState,
  SetupStatus,
} from "../types";
import {
  canFinishSetup,
  canLeaveKeys,
  firstError,
  formatSize,
  gpuSummaryLine,
  initialStep,
  isCounterRecommended,
  keyHint,
  keyOutcomeOk,
  keyRowSummary,
  keysBlockedReason,
  keyTone,
  modeTradeoff,
  restartHint,
  provisionOutcome,
  recommendedMode,
  savedFileName,
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
  it("lands on the key step once provisioned but not settled", () => {
    // W4: a reopened wizard must not skip past keys — that step is what stops a
    // cloud-only install from finishing without the key its next boot needs.
    expect(initialStep("cloud_only", true)).toBe("keys");
  });
  it("goes to the disclosure step once the keys step is settled", () => {
    // W5: "done" is never an entry point — setup_complete is stamped by finishing
    // the disclosure, so a reopened wizard must still pass through it.
    expect(initialStep("cloud_only", true, true)).toBe("disclosure");
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


// -- W4 API-key step ---------------------------------------------------------

const keyRow = (o: Partial<SetupKeyRow>): SetupKeyRow => ({
  env: "OPENROUTER_API_KEY",
  label: "OpenRouter",
  role: "primary",
  signup_url: "https://openrouter.ai/keys",
  prefix: "sk-or-",
  note: "The main cloud brain.",
  required: true,
  present: false,
  masked: "(not set)",
  ...o,
});

const keyResult = (o: Partial<SetupKeyResult>): SetupKeyResult => ({
  env: "OPENROUTER_API_KEY",
  kind: "valid",
  message: "OpenRouter key works.",
  ...o,
});

const keysState = (o: Partial<SetupKeys>): SetupKeys => ({
  mode: "cloud_only",
  keys: [keyRow({})],
  can_finish: { ok: false, missing: "OPENROUTER_API_KEY", message: "needs a key" },
  ...o,
});

describe("keyOutcomeOk", () => {
  it("prefers the save receipt over the validate flag", () => {
    expect(keyOutcomeOk(keyResult({ saved: true, ok: false }))).toBe(true);
    expect(keyOutcomeOk(keyResult({ saved: false, ok: true }))).toBe(false);
  });
  it("falls back to ok for a validate-only result", () => {
    expect(keyOutcomeOk(keyResult({ ok: true }))).toBe(true);
    expect(keyOutcomeOk(keyResult({ ok: false, kind: "invalid_key" }))).toBe(false);
  });
  it("is false before anything has been tried", () => {
    expect(keyOutcomeOk(null)).toBe(false);
  });
});

describe("keyTone", () => {
  it("greens a working key", () => {
    expect(keyTone(keyResult({ ok: true }))).toBe("ok");
  });
  it("warns rather than greens a rate-limited key (it still works)", () => {
    expect(keyTone(keyResult({ ok: true, kind: "rate_limited" }))).toBe("warn");
  });
  it("does NOT blame the key for a network failure", () => {
    // Telling someone their key is bad when their wifi dropped sends them
    // hunting for a new key that will not help.
    expect(keyTone(keyResult({ ok: false, kind: "network" }))).toBe("warn");
    expect(keyTone(keyResult({ ok: false, kind: "server_error" }))).toBe("warn");
  });
  it("errors on a genuinely rejected key", () => {
    expect(keyTone(keyResult({ ok: false, kind: "invalid_key" }))).toBe("error");
    expect(keyTone(keyResult({ ok: false, kind: "no_credit" }))).toBe("error");
  });
  it("is idle with no result yet", () => {
    expect(keyTone(null)).toBe("idle");
  });
});

describe("keyRowSummary", () => {
  it("shows the masked key once set", () => {
    expect(keyRowSummary(keyRow({ present: true, masked: "sk-or-...tail" }))).toBe(
      "sk-or-...tail",
    );
  });
  it("marks an unset key required or optional", () => {
    expect(keyRowSummary(keyRow({ required: true }))).toBe("Required");
    expect(keyRowSummary(keyRow({ required: false }))).toBe("Optional");
  });
});

describe("keyHint", () => {
  it("says nothing for an empty field or a plausible key", () => {
    expect(keyHint(keyRow({}), "")).toBe("");
    expect(keyHint(keyRow({}), "sk-or-v1-longenoughkey")).toBe("");
  });
  it("catches the common paste mistakes", () => {
    expect(keyHint(keyRow({}), "https://openrouter.ai/keys")).toMatch(/URL/);
    expect(keyHint(keyRow({}), "sk-or-a")).toMatch(/short/);
    expect(keyHint(keyRow({}), "sk or v1 key with spaces")).toMatch(/spaces/);
    expect(keyHint(keyRow({}), "nvapi-1234567890abcdef")).toMatch(/sk-or-/);
  });
  it("has no prefix opinion for a vendor without one", () => {
    expect(keyHint(keyRow({ prefix: "" }), "AIzaSyExample1234567")).toBe("");
  });
});

describe("canLeaveKeys / keysBlockedReason", () => {
  it("holds a cloud-only install that has no primary key", () => {
    const s = keysState({});
    expect(canLeaveKeys(s)).toBe(false);
    expect(keysBlockedReason(s)).toBe("needs a key");
  });
  it("lets a satisfied install through", () => {
    const s = keysState({ can_finish: { ok: true, missing: null, message: "" } });
    expect(canLeaveKeys(s)).toBe(true);
    expect(keysBlockedReason(s)).toBe("");
  });
  it("holds before the first fetch resolves", () => {
    expect(canLeaveKeys(null)).toBe(false);
    expect(keysBlockedReason(null)).toBe("");
  });
});


// -- W5 disclosure step ------------------------------------------------------

const doc = (o: Partial<SetupDisclosure>): SetupDisclosure => ({
  mode: "full",
  items: [{ key: "actions", title: "Baby can act", detail: "It can run things." }],
  acknowledged: false,
  ...o,
});

describe("canFinishSetup", () => {
  it("needs the box actually ticked", () => {
    expect(canFinishSetup(doc({}), false)).toBe(false);
    expect(canFinishSetup(doc({}), true)).toBe(true);
  });
  it("holds until the disclosure has loaded", () => {
    // Otherwise the user could acknowledge an empty list.
    expect(canFinishSetup(null, true)).toBe(false);
    expect(canFinishSetup(doc({ items: [] }), true)).toBe(false);
  });
});

describe("restartHint", () => {
  it("tells the user to reopen when a cloud key was just saved", () => {
    const r: SetupComplete = {
      complete: true,
      install_mode: "cloud_only",
      router_mode: "cloud_primary",
      restart_recommended: true,
    };
    expect(restartHint(r)).toMatch(/[Rr]eopen/);
  });
  it("says nothing when no restart is needed", () => {
    expect(restartHint({ complete: true, install_mode: "full" })).toBe("");
    expect(restartHint(null)).toBe("");
  });
  it("says Baby is doing it when the shell restarts the backend itself", () => {
    // The shell owns this process and is bringing it back on the stamped mode, so
    // asking the user to reopen would be telling them to do something already done.
    const r: SetupComplete = {
      complete: true,
      install_mode: "cloud_only",
      router_mode: "cloud_primary",
      restart_recommended: true,
      restarting: true,
    };
    const hint = restartHint(r);
    expect(hint).toMatch(/restarting/i);
    expect(hint).not.toMatch(/[Rr]eopen/);
  });
});

describe("savedFileName", () => {
  it("keeps only the filename, never the path", () => {
    // The repair panel gets screenshotted for help requests; a Windows path
    // carries the user's username.
    const p = String.raw`C:\Users\tanishq\AppData\Local\baby\logs\baby-diagnostics-1.txt`;
    expect(savedFileName(p)).toBe("baby-diagnostics-1.txt");
    expect(savedFileName(p)).not.toMatch(/tanishq/);
  });
  it("handles posix paths and junk", () => {
    expect(savedFileName("/home/x/logs/r.txt")).toBe("r.txt");
    expect(savedFileName("")).toBe("the report");
  });
});
