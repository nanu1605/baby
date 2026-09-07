import { useEffect, useRef, useState } from "react";
import {
  getDiagnostics,
  getSetupHealth,
  getSetupKeys,
  getSetupStatus,
  postAutostart,
  postSetupMode,
  postSetupProvision,
} from "../api/client";
import { useBrain } from "../store";
import {
  firstError,
  provisionOutcome,
  savedFileName,
  rowNote,
  stepGlyph,
  type InstallMode,
} from "../lib/setup";
import type {
  DiagnosticsReport,
  SetupHealth,
  SetupKeyResult,
  SetupKeys,
  SetupStatus,
} from "../types";
import { KeyField } from "./KeyField";

/**
 * Setup & repair — the in-app replacement for an installer Repair/Modify dialog.
 *
 * NSIS has no such dialog (W0 finding), so everything a user would go looking
 * for there lives here instead: re-check what is actually working, re-download
 * whatever broke, switch between Full and cloud-only, see which API keys are
 * configured, and produce a diagnostics report that is safe to post publicly.
 *
 * It reuses the endpoints the first-run wizard already drives, so there is one
 * provisioning path rather than two that can disagree.
 */
export default function RepairPanel() {
  const open = useBrain((s) => s.repairOpen);
  const close = useBrain((s) => s.closeRepair);
  const setup = useBrain((s) => s.stats?.setup);
  const autostart = useBrain((s) => s.stats?.autostart);
  const version = useBrain((s) => s.stats?.version);

  const [health, setHealth] = useState<SetupHealth | null>(null);
  const [checking, setChecking] = useState(false);
  const [keys, setKeys] = useState<SetupKeys | null>(null);
  const [status, setStatus] = useState<SetupStatus | null>(null);
  const [repairing, setRepairing] = useState(false);
  const [diag, setDiag] = useState<DiagnosticsReport | null>(null);
  const [note, setNote] = useState("");
  // Every hook lives ABOVE the `if (!open) return null` below. This one was added
  // under it, next to the handler that uses it, and that alone crashed the whole
  // app the moment anyone opened Settings: the closed panel ran 19 hooks, the open
  // one 20, and React throws "Rendered more hooks than during the previous render"
  // -- an invariant, not a dev-only warning, so the shipped build did it too.
  const [autoBusy, setAutoBusy] = useState(false);
  const alive = useRef(true);

  useEffect(() => {
    alive.current = true;
    return () => {
      alive.current = false;
    };
  }, []);

  useEffect(() => {
    if (!open) return;
    setNote("");
    getSetupKeys()
      .then((k) => alive.current && setKeys(k))
      .catch(() => {});
  }, [open]);

  // Only poll while a repair is actually running — this panel is open on a live
  // app, and an idle 1.2s poll for nothing is rude to a machine that may be
  // loading a model.
  useEffect(() => {
    if (!open || !repairing) return;
    const poll = async () => {
      try {
        const s = await getSetupStatus();
        if (!alive.current) return;
        setStatus(s);
        if (!s.provisioning && provisionOutcome(s) !== "running") {
          setRepairing(false);
        }
      } catch {
        /* transient */
      }
    };
    poll();
    const id = setInterval(poll, 1200);
    return () => clearInterval(id);
  }, [open, repairing]);

  if (!open) return null;

  // A save hands back the refreshed roster, so the list re-renders without another
  // round trip. The restart, when the shell can do one, is already under way by the
  // time this fires -- KeyField says so under the field.
  const applyKeySaved = (r: SetupKeyResult) => {
    if (!alive.current || !r.keys) return;
    setKeys((prev) => (prev ? { ...prev, keys: r.keys! } : prev));
  };

  const runCheck = async () => {
    setChecking(true);
    setNote("");
    try {
      const h = await getSetupHealth();
      if (alive.current) setHealth(h);
    } catch {
      if (alive.current) setNote("Couldn't run the check. Is Baby still starting up?");
    } finally {
      if (alive.current) setChecking(false);
    }
  };

  const runRepair = async () => {
    setNote("");
    const r = await postSetupProvision();
    if (!alive.current) return;
    if (!r.ok) {
      setNote("Couldn't start the repair.");
      return;
    }
    setRepairing(true);
  };

  // The registry is the source of truth, so the button reports what came back
  // rather than assuming the write landed -- the user may have Baby's startup
  // entry blocked from Task Manager, and a toggle that flips anyway would lie.
  const toggleAutostart = async () => {
    if (!autostart?.supported) return;
    if (!autostart.enabled && !autostart.can_enable) return;
    setNote("");
    setAutoBusy(true);
    const now = await postAutostart(!autostart.enabled);
    if (!alive.current) return;
    setAutoBusy(false);
    if (now === autostart.enabled) {
      setNote("Couldn't change the Windows startup setting.");
      return;
    }
    setNote(
      now
        ? "Baby will start with Windows, minimised to the tray. Click the tray icon to open it."
        : "Baby will no longer start with Windows.",
    );
  };

  const switchMode = async (mode: InstallMode) => {
    setNote("");
    const r = await postSetupMode(mode);
    if (!alive.current) return;
    if (!r.ok) {
      setNote("Couldn't switch mode.");
      return;
    }
    // The other mode's dependency set is different, so the switch is only real
    // once the missing pieces are downloaded. Say so, then run it.
    setNote(
      mode === "full"
        ? "Switching to Full — downloading the local model now. This is several GB."
        : "Switched to cloud-only. The local model stays on disk; delete it from the models folder if you want the space back.",
    );
    if (mode === "full") await runRepair();
  };

  const mode = (setup?.install_mode as InstallMode | null) ?? null;
  const progress = status?.progress ?? {};
  const repairError = firstError(progress);

  return (
    <div className="repair-backdrop" role="dialog" aria-modal="true" aria-label="Setup and repair">
      <div className="repair-card">
        <div className="repair-head">
          <h2>Setup &amp; repair</h2>
          <button type="button" className="repair-close" onClick={close} aria-label="Close">
            ✕
          </button>
        </div>

        {/* Which build is running, before anything else in the dialog.
            A 6.0.2 installer reported success over an untouched 6.0.0 install and
            replaced nothing. The app showed its version nowhere, so the stale install
            was indistinguishable from a current one and the features it was missing
            read as features that were never built. One line ends that class of report.

            `shell` is null when the shell attached to a backend it did not spawn --
            unknown, and deliberately NOT rendered as a mismatch. */}
        {version && (
          <p className="repair-version">
            Baby <strong>{version.app}</strong>
            {version.shell && version.shell !== version.app && (
              <span className="repair-bad">
                {" "}— but the app window is {version.shell}. An update only half
                applied. Quit Baby from the tray and run the installer again.
              </span>
            )}
          </p>
        )}

        {/* First, and under its own name.
            This shipped inside "How Baby runs", below two buttons about local vs
            cloud, four sections down a scrolling dialog. It was reported as missing
            from the build that contains it, which is the same thing as missing. A
            preference the user chooses is not a repair action and does not belong
            filed behind one. */}
        {autostart?.supported && (
          <section className="repair-section">
            <h3>Start with Windows</h3>
            <p className="repair-note">
              {autostart.enabled
                ? "Baby starts automatically when you sign in, minimised to the tray. Click the tray icon to open it."
                : "Baby only starts when you open it. Turn this on and it will be ready every time you sign in, waiting in the tray rather than on screen."}
            </p>
            {/* Turning it OFF is always offered when it is on, even where Baby
                cannot turn it on: a setting you can switch on and not off is the
                bug this release is named after. */}
            {!autostart.enabled && !autostart.can_enable && (
              <p className="repair-note">
                This copy of Baby is running from a checkout rather than the
                installed app, so it has no shortcut to add to startup.
              </p>
            )}
            <div className="repair-actions">
              <button
                type="button"
                className="repair-btn"
                disabled={autoBusy || (!autostart.enabled && !autostart.can_enable)}
                onClick={toggleAutostart}
              >
                {autostart.enabled
                  ? "Don't start with Windows"
                  : "Start Baby with Windows"}
              </button>
            </div>
          </section>
        )}

        <section className="repair-section">
          <h3>Is everything working?</h3>
          <p className="repair-note">
            Loads each model and runs a real operation — not just "is the file
            there". Takes a few seconds.
          </p>
          <button type="button" className="repair-btn" disabled={checking} onClick={runCheck}>
            {checking ? "Checking…" : "Run a check"}
          </button>
          {health && (
            <>
              <p className={health.ok ? "repair-ok" : "repair-bad"}>{health.summary}</p>
              {!health.ok && (
                <ul className="repair-list">
                  {health.results
                    .filter((r) => !r.ok)
                    .map((r) => (
                      <li key={r.name}>
                        <strong>{r.name}</strong> — {r.detail}
                      </li>
                    ))}
                </ul>
              )}
            </>
          )}
        </section>

        <section className="repair-section">
          <h3>Repair</h3>
          <p className="repair-note">
            Re-downloads anything missing or damaged. Already-installed pieces are
            skipped, and interrupted downloads resume.
          </p>
          <button
            type="button"
            className="repair-btn"
            disabled={repairing}
            onClick={runRepair}
          >
            {repairing ? "Repairing…" : "Repair install"}
          </button>
          {repairing && (
            <ul className="repair-list">
              {Object.keys(progress).map((k) => (
                <li key={k}>
                  {stepGlyph(progress[k].status)} {k}
                  {progress[k].human
                    ? ` — ${progress[k].human}`
                    : rowNote(progress[k].status, progress[k])
                      ? ` — ${rowNote(progress[k].status, progress[k])}`
                      : ""}
                </li>
              ))}
            </ul>
          )}
          {repairError && <p className="repair-bad">{repairError}</p>}
        </section>

        <section className="repair-section">
          <h3>How Baby runs</h3>
          <p className="repair-note">
            Currently <strong>{mode === "cloud_only" ? "cloud only" : "Full (local + cloud)"}</strong>.
          </p>
          <div className="repair-actions">
            <button
              type="button"
              className="repair-btn"
              disabled={mode === "full" || repairing}
              onClick={() => switchMode("full")}
            >
              Switch to Full
            </button>
            <button
              type="button"
              className="repair-btn"
              disabled={mode === "cloud_only" || repairing}
              onClick={() => switchMode("cloud_only")}
            >
              Switch to cloud only
            </button>
          </div>
        </section>

        <section className="repair-section">
          <h3>API keys</h3>
          <div className="wizard-keys">
            {(keys?.keys ?? []).map((row) => (
              <KeyField key={row.env} row={row} onSaved={applyKeySaved} />
            ))}
          </div>
          <p className="repair-note">
            Each key is checked against its provider before it is stored, in a file
            only your account can read. Saving one here switches Baby onto it — the
            router is built at boot, so Baby restarts itself to pick it up.
          </p>
        </section>

        <section className="repair-section">
          <h3>Report a problem</h3>
          <p className="repair-note">
            Generates a report you can paste into an issue. Your API keys, your
            Windows username and your name are removed before you see it.
          </p>
          <div className="repair-actions">
            <button
              type="button"
              className="repair-btn"
              onClick={() =>
                getDiagnostics(true)
                  .then((d) => alive.current && setDiag(d))
                  .catch(() => alive.current && setNote("Couldn't build the report."))
              }
            >
              Create report
            </button>
            {diag && (
              <button
                type="button"
                className="repair-btn"
                onClick={() => navigator.clipboard?.writeText(diag.text).catch(() => {})}
              >
                Copy
              </button>
            )}
          </div>
          {diag?.saved_to && (
            // Filename only. The full path contains the Windows username, and
            // this panel is the one users screenshot when asking for help — the
            // report text is scrubbed, so the chrome around it must be too.
            <p className="repair-note">
              Saved as <code>{savedFileName(diag.saved_to)}</code> in Baby's logs
              folder.
            </p>
          )}
          {diag && <pre className="repair-report">{diag.text}</pre>}
        </section>

        {note && <p className="repair-note repair-emphasis">{note}</p>}
      </div>
    </div>
  );
}
