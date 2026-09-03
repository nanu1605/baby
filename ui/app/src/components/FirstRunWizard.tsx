import { useEffect, useRef, useState } from "react";
import {
  getSetupDisclosure,
  getSetupGpu,
  getSetupKeys,
  getSetupPlan,
  getSetupStatus,
  postSetupComplete,
  postSetupMode,
  postSetupProvision,
} from "../api/client";
import { useBrain } from "../store";
import { KeyField } from "./KeyField";
import {
  canFinishSetup,
  canLeaveKeysStep,
  firstError,
  formatSize,
  gpuSummaryLine,
  initialStep,
  isCounterRecommended,
  keysBlockedReason,
  modeTradeoff,
  provisionOutcome,
  recommendedMode,
  restartHint,
  rowBar,
  rowStatus,
  stepGlyph,
  unsavedKeyLabels,
  unsavedKeysWarning,
  type InstallMode,
  type WizardStep,
} from "../lib/setup";
import type {
  SetupComplete,
  SetupDisclosure,
  SetupGpu,
  SetupKeyResult,
  SetupKeys,
  SetupStatus,
  SetupStep,
} from "../types";

/**
 * v6 first-run wizard. A full-screen overlay shown only in an installed build with
 * setup unfinished (App gates it via shouldShowWizard, so a dev checkout never sees
 * it).
 *
 * mode (W2) → provision (W3) → keys (W4) → disclosure (W5) → done. The disclosure
 * step is what finally stamps `setup_complete`: until the user has been shown what
 * Baby can do and acknowledged it, the wizard never claims to be finished, so
 * closing early means being asked again rather than being silently marked set up.
 */
export default function FirstRunWizard() {
  const installMode = useBrain((s) => s.stats?.setup?.install_mode ?? null);
  const provisioned = useBrain((s) => s.stats?.setup?.provisioned ?? false);
  const [step, setStep] = useState<WizardStep>(() => initialStep(installMode, provisioned));
  const [gpu, setGpu] = useState<SetupGpu | null>(null);
  const [loadError, setLoadError] = useState(false);
  const [busy, setBusy] = useState<InstallMode | null>(null);
  const [postError, setPostError] = useState(false);
  const [chosen, setChosen] = useState<InstallMode | null>(null);
  const [finish, setFinish] = useState<SetupComplete | null>(null);
  // Guards every post-await setState so a mid-flight unmount (e.g. /stats flips
  // setup.complete while a POST is pending) can't set state on a dead component.
  const alive = useRef(true);
  useEffect(() => {
    alive.current = true;
    return () => {
      alive.current = false;
    };
  }, []);

  useEffect(() => {
    getSetupGpu()
      .then((g) => alive.current && setGpu(g))
      .catch(() => alive.current && setLoadError(true));
  }, []);

  const choose = async (mode: InstallMode) => {
    setBusy(mode);
    setPostError(false);
    try {
      const r = await postSetupMode(mode);
      if (!alive.current) return;
      if (!r.ok) throw new Error(String(r.status));
      setChosen(mode);
      setStep("provision");
    } catch {
      if (alive.current) setPostError(true);
    } finally {
      if (alive.current) setBusy(null);
    }
  };

  const checking = gpu === null && !loadError;

  return (
    <div
      className="wizard-overlay"
      role="dialog"
      aria-modal="true"
      aria-label="Baby first-run setup"
    >
      <div className="wizard-card">
        {step === "mode" ? (
          <>
            <h2>Welcome to Baby</h2>
            <p className="wizard-sub">
              Choose how Baby runs. You can change this later in settings.
            </p>

            <p className="wizard-gpu">
              {checking && "Checking your GPU…"}
              {loadError && "Couldn't read your GPU — pick whichever fits your machine."}
              {gpu && gpuSummaryLine(gpu)}
            </p>

            <div className="wizard-modes">
              <ModeCard
                mode="full"
                gpu={gpu}
                disabled={checking || busy !== null}
                busy={busy === "full"}
                onChoose={choose}
              />
              <ModeCard
                mode="cloud_only"
                gpu={gpu}
                disabled={checking || busy !== null}
                busy={busy === "cloud_only"}
                onChoose={choose}
              />
            </div>

            {postError && (
              <p className="wizard-err">
                Couldn't save that choice. Check your connection and try again.
              </p>
            )}
          </>
        ) : step === "provision" ? (
          <ProvisionStep onDone={() => setStep("keys")} />
        ) : step === "keys" ? (
          <KeysStep onDone={() => setStep("disclosure")} />
        ) : step === "disclosure" ? (
          <DisclosureStep
            onDone={(r) => {
              setFinish(r);
              setStep("done");
            }}
          />
        ) : (
          <DoneStep
            mode={chosen ?? (installMode as InstallMode | null)}
            finish={finish}
            onContinue={() => useBrain.getState().dismissWizard()}
          />
        )}
      </div>
    </div>
  );
}

function ProvisionStep({ onDone }: { onDone: () => void }) {
  const [plan, setPlan] = useState<SetupStep[]>([]);
  const [status, setStatus] = useState<SetupStatus | null>(null);
  const [postError, setPostError] = useState(false);
  const alive = useRef(true);
  const started = useRef(false);

  const kickoff = async () => {
    if (!alive.current) return;
    setPostError(false);
    started.current = true;
    const r = await postSetupProvision();
    if (alive.current && !r.ok) setPostError(true);
  };

  useEffect(() => {
    alive.current = true;
    getSetupPlan()
      .then((p) => alive.current && setPlan(p.steps))
      .catch(() => {});
    return () => {
      alive.current = false;
    };
  }, []);

  // Poll status; kick off the run once if nothing is going yet (a re-entry mid-run
  // just resumes polling without re-POSTing).
  useEffect(() => {
    const poll = async () => {
      try {
        const s = await getSetupStatus();
        if (!alive.current) return;
        setStatus(s);
        if (!started.current && !s.provisioning && provisionOutcome(s) === "idle") {
          await kickoff();
        }
      } catch {
        /* transient — next tick retries */
      }
    };
    poll();
    const id = setInterval(poll, 1200);
    return () => clearInterval(id);
  }, []);

  const outcome = provisionOutcome(status);
  useEffect(() => {
    if (outcome === "done") onDone();
  }, [outcome, onDone]);

  const progress = status?.progress ?? {};
  return (
    <>
      <h2>Setting up Baby</h2>
      <p className="wizard-sub">
        Downloading and checking everything Baby needs. The first run can take a
        while — you can leave this open.
      </p>

      <ul className="wizard-steps">
        {plan.map((s) => {
          const st = rowStatus(s.key, progress);
          const bar = rowBar(progress[s.key]);
          const size = formatSize(s.size_mb);
          return (
            <li key={s.key} className={`wizard-step-row status-${st}`}>
              <span className="wizard-step-icon">{stepGlyph(st)}</span>
              <span className="wizard-step-label">
                {s.label}
                {size && <span className="wizard-step-size"> · {size}</span>}
                {!s.required && <span className="wizard-step-size"> · optional</span>}
              </span>
              {bar ? (
                <span className="wizard-step-bar">
                  <span className="wizard-step-fill" style={{ width: `${bar.pct}%` }} />
                  <span className="wizard-step-pct">{bar.label}</span>
                </span>
              ) : (
                <span className="wizard-step-state">{st === "pending" ? "" : st}</span>
              )}
            </li>
          );
        })}
      </ul>

      {outcome === "error" && (
        <>
          <p className="wizard-err">
            Something didn't finish. {firstError(progress)}
          </p>
          <div className="wizard-actions">
            <button type="button" className="wizard-primary" onClick={kickoff}>
              Retry
            </button>
          </div>
        </>
      )}
      {postError && (
        <p className="wizard-err">Couldn't start setup. Check your connection and try again.</p>
      )}
    </>
  );
}

/**
 * W4 key step. Every key is proved against its vendor before it is saved, so a
 * typo is caught here rather than at the first cloud escalation.
 *
 * A cloud-only install cannot leave this step without the primary key: with no
 * local model on the machine, finishing keyless would boot into a router that
 * refuses to build. The server decides that (`can_finish`) and the UI mirrors it
 * rather than keeping its own rule.
 */
function KeysStep({ onDone }: { onDone: () => void }) {
  const [keys, setKeys] = useState<SetupKeys | null>(null);
  const [loadError, setLoadError] = useState(false);
  // Envs with text sitting in the box. Continue refuses to move past them: a
  // tested-but-unsaved key was silently thrown away here, and the install booted
  // local-only with the user believing the green tick had stored it.
  const [pending, setPending] = useState<string[]>([]);
  const alive = useRef(true);

  useEffect(() => {
    alive.current = true;
    getSetupKeys()
      .then((k) => alive.current && setKeys(k))
      .catch(() => alive.current && setLoadError(true));
    return () => {
      alive.current = false;
    };
  }, []);

  // A save returns the refreshed roster, so the step re-gates itself without
  // another round trip.
  const applySaved = (r: SetupKeyResult) => {
    if (!alive.current || !r.keys || !r.can_finish) return;
    setKeys((prev) =>
      prev ? { ...prev, keys: r.keys!, can_finish: r.can_finish! } : prev,
    );
  };

  const notePending = (env: string, isPending: boolean) =>
    setPending((prev) =>
      isPending ? (prev.includes(env) ? prev : [...prev, env]) : prev.filter((e) => e !== env),
    );

  const blocked = keysBlockedReason(keys);
  const unsaved = unsavedKeysWarning(unsavedKeyLabels(keys, pending));
  const cloudOnly = keys?.mode === "cloud_only";

  return (
    <>
      <h2>Connect a cloud brain</h2>
      <p className="wizard-sub">
        {cloudOnly
          ? "This install runs entirely on cloud models, so it needs at least the main key."
          : "Optional — Baby already has a local brain. Adding a key lets it reach for a stronger cloud model."}
      </p>

      {loadError && (
        <p className="wizard-err">
          Couldn't load the key settings. Reopen Baby and try again.
        </p>
      )}

      <div className="wizard-keys">
        {(keys?.keys ?? []).map((row) => (
          <KeyField
            key={row.env}
            row={row}
            onSaved={applySaved}
            onPendingChange={notePending}
          />
        ))}
      </div>

      {blocked && <p className="wizard-warn">{blocked}</p>}
      {unsaved && <p className="wizard-warn">{unsaved}</p>}

      <div className="wizard-actions">
        <button
          type="button"
          className="wizard-primary"
          disabled={!canLeaveKeysStep(keys, pending)}
          onClick={onDone}
        >
          Continue
        </button>
      </div>
    </>
  );
}

function ModeCard({
  mode,
  gpu,
  disabled,
  busy,
  onChoose,
}: {
  mode: InstallMode;
  gpu: SetupGpu | null;
  disabled: boolean;
  busy: boolean;
  onChoose: (m: InstallMode) => void;
}) {
  const title = mode === "full" ? "Full — local + cloud" : "Cloud only";
  const recommended = gpu ? recommendedMode(gpu) === mode : false;
  const counter = gpu ? isCounterRecommended(gpu, mode) : false;

  return (
    <button
      type="button"
      className={`wizard-mode${recommended ? " recommended" : ""}`}
      disabled={disabled}
      onClick={() => onChoose(mode)}
    >
      <div className="wizard-mode-head">
        <span className="wizard-mode-title">{title}</span>
        {recommended && <span className="wizard-pill">Recommended</span>}
      </div>
      <p className="wizard-mode-blurb">{modeTradeoff(mode)}</p>
      {counter && !recommended && (
        <p className="wizard-warn">
          {mode === "full"
            ? "Your GPU is below the 8 GB bar — the local brain may be slow or fail to load."
            : "You have a capable GPU — Full would also give you an offline brain."}
        </p>
      )}
      {busy && <span className="wizard-busy">Saving…</span>}
    </button>
  );
}

/**
 * W5 disclosure. The EULA covered this legally at install time, when nobody
 * reads; this is the same substance at the moment it matters, in an owner's
 * words, with an acknowledgement recorded in setup.json.
 *
 * Finishing here is what finally stamps `setup_complete` — until this step the
 * wizard has never claimed to be done, so a user who closed it early gets asked
 * again rather than being silently marked set up.
 */
function DisclosureStep({ onDone }: { onDone: (r: SetupComplete) => void }) {
  const [doc, setDoc] = useState<SetupDisclosure | null>(null);
  const [ack, setAck] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const alive = useRef(true);

  useEffect(() => {
    alive.current = true;
    getSetupDisclosure()
      .then((d) => alive.current && setDoc(d))
      .catch(() => alive.current && setError("Couldn't load this. Reopen Baby."));
    return () => {
      alive.current = false;
    };
  }, []);

  const finish = async () => {
    setBusy(true);
    setError("");
    try {
      const r = await postSetupComplete();
      if (alive.current) onDone(r);
    } catch {
      // The server refuses an unkeyed cloud-only finish too — say what to do.
      if (alive.current) {
        setError("Couldn't finish setup. Check that your cloud key is saved.");
      }
    } finally {
      if (alive.current) setBusy(false);
    }
  };

  return (
    <>
      <h2>Before you start</h2>
      <p className="wizard-sub">
        What Baby can do on this PC, in plain terms. Worth thirty seconds.
      </p>

      <ul className="wizard-disclosure">
        {(doc?.items ?? []).map((i) => (
          <li key={i.key}>
            <span className="wizard-disclosure-title">{i.title}</span>
            <span className="wizard-disclosure-detail">{i.detail}</span>
          </li>
        ))}
      </ul>

      <label className="wizard-ack">
        <input
          type="checkbox"
          checked={ack}
          onChange={(e) => setAck(e.target.checked)}
        />
        <span>I understand what Baby can do on this PC.</span>
      </label>

      {error && <p className="wizard-err">{error}</p>}

      <div className="wizard-actions">
        <button
          type="button"
          className="wizard-primary"
          disabled={!canFinishSetup(doc, ack) || busy}
          onClick={finish}
        >
          {busy ? "Finishing…" : "Finish setup"}
        </button>
      </div>
    </>
  );
}

function DoneStep({
  mode,
  finish,
  onContinue,
}: {
  mode: InstallMode | null;
  finish: SetupComplete | null;
  onContinue: () => void;
}) {
  const label = mode === "cloud_only" ? "Cloud only" : "Full (local + cloud)";
  const restart = restartHint(finish);
  return (
    <>
      <h2>Baby is ready</h2>
      <p className="wizard-sub">
        Everything Baby needs is installed and verified. Running in{" "}
        <strong>{label}</strong> mode.
      </p>
      <p className="wizard-note">
        Any key you saved is stored on this PC only, in a file just you can read.
        {restart ? ` ${restart}` : ""}
      </p>
      <div className="wizard-actions">
        <button type="button" className="wizard-primary" onClick={onContinue}>
          Start using Baby
        </button>
      </div>
    </>
  );
}
