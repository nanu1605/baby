/**
 * First-run wizard: the pure decisions, kept out of the component so vitest can
 * cover them (the codebase tests logic, not JSX). The component in
 * components/FirstRunWizard.tsx renders these.
 */
import type { SetupGpu, SetupState } from "../types";

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
 * Where the wizard opens: if a mode was already chosen on a prior launch (the app
 * was closed before finishing), skip straight past the mode fork rather than
 * re-asking. Grows as W3/W4/W5 add steps.
 */
export function initialStep(installMode: string | null | undefined): WizardStep {
  return installMode ? "done" : "mode";
}

export type WizardStep = "mode" | "done";

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
