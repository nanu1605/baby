import { useEffect, useRef, useState } from "react";
import {
  getSetupGpu,
  getSetupPlan,
  getSetupStatus,
  postSetupMode,
  postSetupProvision,
} from "../api/client";
import { useBrain } from "../store";
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
  stepGlyph,
  type InstallMode,
  type WizardStep,
} from "../lib/setup";
import type { SetupGpu, SetupStatus, SetupStep } from "../types";

/**
 * v6 first-run wizard — W2 slice: GPU pre-check + install-mode fork. A full-screen
 * overlay shown only in an installed build with setup unfinished (App gates it via
 * shouldShowWizard, so a dev checkout never sees it). W3 (deps) / W4 (keys) / W5
 * (disclosure) slot their steps in ahead of the terminal panel, which is where
 * setup_complete finally gets stamped. Until then the terminal panel just frees the
 * current session (dismissWizard) — it never fakes completion.
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
          <ProvisionStep onDone={() => setStep("done")} />
        ) : (
          <DoneStep
            mode={chosen ?? (installMode as InstallMode | null)}
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

function DoneStep({
  mode,
  onContinue,
}: {
  mode: InstallMode | null;
  onContinue: () => void;
}) {
  const label = mode === "cloud_only" ? "Cloud only" : "Full (local + cloud)";
  return (
    <>
      <h2>Baby is ready</h2>
      <p className="wizard-sub">
        Everything Baby needs is installed and verified. Running in{" "}
        <strong>{label}</strong> mode.
      </p>
      <p className="wizard-note">
        Adding API keys comes next — that step isn't wired up in this build yet. For
        now you can start using Baby.
      </p>
      <div className="wizard-actions">
        <button type="button" className="wizard-primary" onClick={onContinue}>
          Start using Baby
        </button>
      </div>
    </>
  );
}
