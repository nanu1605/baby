/**
 * First-run wizard: the pure decisions, kept out of the component so vitest can
 * cover them (the codebase tests logic, not JSX). The component in
 * components/FirstRunWizard.tsx renders these.
 */
import type { SetupGpu, SetupProgressEvent, SetupState, SetupStatus } from "../types";

export type InstallMode = "full" | "cloud_only";

/**
 * Show the wizard only in an INSTALLED build whose setup isn't finished and that
 * the user hasn't dismissed this session. A dev checkout reports `installed:false`
 * (no BABY_HOME) so it never sees the wizard, even though `complete` is also false.
 */
export function shouldShowWizard(
  setup: SetupState | undefined,
  dismissed: boolean,
): boolean {
  if (!setup || dismissed) return false;
  return setup.installed && !setup.complete;
}

/**
 * Where the wizard opens on (re-)entry: no mode yet → the fork; a mode chosen but
 * deps not provisioned → the provisioning step (resume it); fully provisioned →
 * the terminal panel. Grows as W4/W5 add steps.
 */
export function initialStep(
  installMode: string | null | undefined,
  provisioned = false,
): WizardStep {
  if (!installMode) return "mode";
  return provisioned ? "done" : "provision";
}

export type WizardStep = "mode" | "provision" | "done";

/** One-line GPU summary for the mode screen. */
export function gpuSummaryLine(gpu: SetupGpu): string {
  if (!gpu.has_nvidia || gpu.vram_total_gb == null) {
    return "No NVIDIA GPU detected";
  }
  const name = gpu.gpu_name ?? "NVIDIA GPU";
  return `${name} · ${gpu.vram_total_gb.toFixed(1)} GB VRAM`;
}

export function recommendedMode(gpu: SetupGpu): InstallMode {
  return gpu.recommend === "full" ? "full" : "cloud_only";
}

/**
 * True when the pick contradicts the GPU recommendation, so the UI can warn in
 * plain language — but never blocks. A capable GPU may still pick cloud-only; a
 * weak GPU may force Full (spec §W2: the user makes the final call).
 */
export function isCounterRecommended(gpu: SetupGpu, mode: InstallMode): boolean {
  return mode !== recommendedMode(gpu);
}

/** The plain-language tradeoff shown under each mode choice. */
export function modeTradeoff(mode: InstallMode): string {
  return mode === "full"
    ? "Local 9B brain + cloud. Works offline; your chats can stay on this PC. Downloads a few GB now."
    : "Cloud only — no local brain. Fastest to set up, but needs internet and an API key, and chats go to the cloud.";
}

// -- W3 provisioning step ----------------------------------------------------

const _DONE = new Set(["done", "present", "pass"]);
const _ERROR = new Set(["error", "fail"]);

/** Human size for a checklist row: "1.6 GB" / "310 MB" / "" (0 = no size to show). */
export function formatSize(mb: number): string {
  if (!mb) return "";
  return mb >= 1024 ? `${(mb / 1024).toFixed(1)} GB` : `${mb} MB`;
}

/** Overall state of a provisioning run, from the /status snapshot. */
export function provisionOutcome(
  status: SetupStatus | null,
): "idle" | "running" | "done" | "error" {
  if (!status) return "idle";
  if (status.provisioning) return "running";
  const progress = status.progress ?? {};
  const keys = Object.keys(progress);
  if (keys.length === 0) return "idle";
  if (progress.verify?.status === "pass") return "done";
  if (keys.some((k) => _ERROR.has(progress[k].status))) return "error";
  return "running"; // between kickoff and the first tick, or a transient gap
}

/** Status to show for a plan row — "pending" until the backend emits its first event. */
export function rowStatus(
  key: string,
  progress: Record<string, SetupProgressEvent>,
): string {
  return progress[key]?.status ?? "pending";
}

/** The progress bar for an actively-downloading row, else null. */
export function rowBar(
  ev: SetupProgressEvent | undefined,
): { pct: number; label: string } | null {
  if (!ev || ev.status !== "working" || ev.pct == null) return null;
  return { pct: ev.pct, label: ev.human ?? `${ev.pct}%` };
}

/** Glyph for a row status (checklist icon). */
export function stepGlyph(status: string): string {
  if (_DONE.has(status)) return "✓";
  if (_ERROR.has(status)) return "✕";
  if (status === "skip") return "–";
  if (status === "needs_install") return "→";
  if (status === "working") return "↓";
  return "○"; // pending
}

/** First failing step's message, for the error banner (never a raw trace). */
export function firstError(progress: Record<string, SetupProgressEvent>): string {
  for (const k of Object.keys(progress)) {
    const ev = progress[k];
    if (_ERROR.has(ev.status)) return ev.message ?? ev.detail ?? `${k} failed`;
  }
  return "";
}
