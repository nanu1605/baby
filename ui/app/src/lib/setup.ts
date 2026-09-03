/**
 * First-run wizard: the pure decisions, kept out of the component so vitest can
 * cover them (the codebase tests logic, not JSX). The component in
 * components/FirstRunWizard.tsx renders these.
 */
import type {
  SetupComplete,
  SetupDisclosure,
  SetupGpu,
  SetupKeyResult,
  SetupKeyRow,
  SetupKeys,
  SetupProgressEvent,
  SetupState,
  SetupStatus,
} from "../types";

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
 * deps not provisioned → the provisioning step (resume it); provisioned but the
 * key step not settled → keys; everything settled → the terminal panel.
 *
 * `keysSettled` defaults to false so a reopened wizard lands on the key step
 * rather than skipping past it — that step is where a cloud-only install is
 * stopped from finishing without the key its next boot requires.
 */
export function initialStep(
  installMode: string | null | undefined,
  provisioned = false,
  keysSettled = false,
): WizardStep {
  if (!installMode) return "mode";
  if (!provisioned) return "provision";
  return keysSettled ? "disclosure" : "keys";
}

export type WizardStep = "mode" | "provision" | "keys" | "disclosure" | "done";

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

/**
 * The words beside a row that has no byte bar.
 *
 * The steps with no Content-Length -- the two huggingface loaders and the
 * wake-word download -- rendered as the bare word "working" for their whole run,
 * because `rowBar` needs a `pct` they cannot produce and nothing else showed the
 * `detail` the backend was already sending. Six minutes of that on the wake-word
 * row was reported as a hung install. The detail says how many MB have landed and
 * for how long nothing has moved, which is the entire difference between a slow
 * download and a wedged one.
 */
export function rowNote(status: string, ev: SetupProgressEvent | undefined): string {
  if (status === "pending") return "";
  if (status === "working" && ev?.detail) return ev.detail;
  return status;
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

// -- W4 API-key step ---------------------------------------------------------

/**
 * Whether a validation/save outcome should read as success.
 *
 * `rate_limited` counts as a PASS: a 429 means the vendor recognised the key and
 * is throttling, so rejecting it would trap a rate-limited user in the wizard
 * with no way forward. `cleared` is the deliberate removal of a key.
 */
export function keyOutcomeOk(result: SetupKeyResult | null): boolean {
  if (!result) return false;
  if (typeof result.saved === "boolean") return result.saved;
  return result.ok === true;
}

/**
 * The tone a key result should be shown in. A network failure is deliberately
 * NOT an error against the key — telling someone their key is bad when their
 * wifi dropped sends them hunting for a new key that will not help.
 */
export function keyTone(result: SetupKeyResult | null): "ok" | "warn" | "error" | "idle" {
  if (!result) return "idle";
  if (keyOutcomeOk(result)) return result.kind === "rate_limited" ? "warn" : "ok";
  if (result.kind === "network" || result.kind === "server_error") return "warn";
  return "error";
}

/** What a row shows on the right: the masked key, or a nudge to add one. */
export function keyRowSummary(row: SetupKeyRow): string {
  if (row.present) return row.masked;
  return row.required ? "Required" : "Optional";
}

/**
 * Client-side paste hint, advisory only — the vendor probe is the real
 * authority, so this may warn but must never block a submit. Catches the
 * common paste errors: a URL, a truncated key, or the wrong vendor's key.
 */
export function keyHint(row: SetupKeyRow, value: string): string {
  const v = value.trim();
  if (!v) return "";
  if (/\s/.test(v)) return "That contains spaces — check the paste.";
  if (v.startsWith("http")) return "That looks like a URL, not a key.";
  if (v.length < 12) return "That looks too short to be a key.";
  if (row.prefix && !v.startsWith(row.prefix)) {
    return `${row.label} keys usually start with "${row.prefix}".`;
  }
  return "";
}

/**
 * Whether the wizard may leave the key step.
 *
 * Mirrors the server's can_finish rather than second-guessing it: a cloud-only
 * install with no primary key would boot straight into a crash, so the step
 * holds. Full may continue keyless and stay on the local brain.
 */
export function canLeaveKeys(keys: SetupKeys | null): boolean {
  return keys?.can_finish?.ok ?? false;
}

/** Why the step is blocking, for the banner. "" when it is not blocking. */
export function keysBlockedReason(keys: SetupKeys | null): string {
  if (!keys || keys.can_finish?.ok) return "";
  return keys.can_finish?.message ?? "A cloud key is required to continue.";
}

/**
 * Did the last action prove the key without storing it?
 *
 * "Test" hits the vendor and deliberately writes nothing, and its success message
 * is "<vendor> key works." — which reads exactly like "you're done". A real
 * install finished the wizard that way: key tested, green tick, Continue, and the
 * key gone. Nothing was written to .env, so router_mode stayed unstamped and Baby
 * booted local-only with the user believing it was on the cloud.
 */
export function keyTestedNotSaved(result: SetupKeyResult | null, hasText: boolean): boolean {
  if (!result || !hasText) return false;
  return result.saved === undefined && result.ok === true;
}

/**
 * The labels of every key that has been typed but not saved. The wizard holds on
 * these rather than discarding them silently when the step is left.
 */
export function unsavedKeyLabels(
  keys: SetupKeys | null,
  pending: readonly string[],
): string[] {
  const byEnv = new Map((keys?.keys ?? []).map((r) => [r.env, r.label]));
  return pending.map((env) => byEnv.get(env) ?? env);
}

/** The banner for those unsaved keys. "" when there are none. */
export function unsavedKeysWarning(labels: readonly string[]): string {
  if (labels.length === 0) return "";
  const which = labels.join(" and ");
  return `Your ${which} key is typed in but not saved yet — press Save to store it, or clear the box.`;
}

/**
 * The key step may be left when the server is satisfied AND nothing is sitting
 * unsaved in a box. Testing a key is not saving it, and Continue used to take no
 * notice of the difference.
 */
export function canLeaveKeysStep(
  keys: SetupKeys | null,
  pending: readonly string[],
): boolean {
  return canLeaveKeys(keys) && pending.length === 0;
}

/**
 * What a saved key means for the running process. The router is built once, at
 * boot, from the key state as it was then — so a key saved later does nothing
 * until something restarts the backend. The server says which case this is.
 */
export function keySavedHint(result: SetupKeyResult | null): string {
  if (!result?.saved) return "";
  if (result.restarting) {
    return "Baby is restarting to start using this key. This takes a few seconds.";
  }
  if (result.restart_required) return "Reopen Baby once to start using this key.";
  return "";
}


// -- W5 disclosure step ------------------------------------------------------

/**
 * The wizard may only finish once the user has actually ticked the box. The
 * server refuses an unacknowledged complete too -- this just keeps the button
 * honest rather than letting it fail on click.
 */
export function canFinishSetup(
  disclosure: SetupDisclosure | null,
  acknowledged: boolean,
): boolean {
  return Boolean(disclosure && disclosure.items.length > 0 && acknowledged);
}

/**
 * A saved cloud key only takes effect on the next launch: .env is read at boot
 * and the router is built once.
 *
 * When the shell owns this backend it restarts it right here, so say that instead
 * of asking for something the user does not have to do. Leaving it as advice is how
 * a real install ended up with a valid, working OpenRouter key, a stamped
 * cloud_primary, and a running process that had neither a cloud brain nor a
 * game-mode button — both reading as broken features rather than as a pending
 * restart nobody noticed one line of text about.
 */
export function restartHint(result: SetupComplete | null): string {
  if (result?.restarting) {
    return "Applying your setup — Baby is restarting itself. This takes a few seconds.";
  }
  if (!result?.restart_recommended) return "";
  return "Reopen Baby once to switch over to the cloud brain.";
}

/**
 * The bare filename from a saved-report path.
 *
 * The repair panel is exactly the screen a user screenshots when asking for
 * help, and a Windows path carries their username. The report body is scrubbed,
 * so the text around it has to be too — showing the folder buys nothing the
 * panel does not already say.
 */
export function savedFileName(path: string): string {
  const parts = String(path || "").split(/[\\/]/);
  return parts[parts.length - 1] || "the report";
}
