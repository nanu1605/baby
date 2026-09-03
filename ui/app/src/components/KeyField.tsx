/**
 * One API key: a masked summary, a password field, a real vendor check, and a save.
 *
 * Shared by the first-run wizard and the repair panel, because "finish setup" must
 * not be the last moment a key can be added. It was: the panel listed the keys
 * read-only and told the user to hand-edit .env, which also leaves router_mode
 * unstamped, so even a correctly edited file left Baby on the local brain.
 *
 * The field is type=password and is cleared the moment a save succeeds — the raw
 * key exists in this component only between paste and save. Everything the server
 * sends back is already masked.
 */
import { useEffect, useRef, useState } from "react";

import { postSetupKey, postSetupKeyValidate } from "../api/client";
import {
  keyHint,
  keyOutcomeOk,
  keyRowSummary,
  keySavedHint,
  keyTestedNotSaved,
  keyTone,
} from "../lib/setup";
import type { SetupKeyResult, SetupKeyRow } from "../types";

export function KeyField({
  row,
  onSaved,
  onPendingChange,
}: {
  row: SetupKeyRow;
  onSaved: (r: SetupKeyResult) => void;
  /** Fires whenever the box goes from empty to typed-in or back, so the caller can
   *  refuse to move on while a key is sitting there unsaved. */
  onPendingChange?: (env: string, pending: boolean) => void;
}) {
  const [value, setValue] = useState("");
  const [result, setResult] = useState<SetupKeyResult | null>(null);
  const [busy, setBusy] = useState<"test" | "save" | null>(null);
  const alive = useRef(true);
  useEffect(() => {
    alive.current = true;
    return () => {
      alive.current = false;
      onPendingChange?.(row.env, false); // an unmounted field holds nothing
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const change = (next: string) => {
    setValue(next);
    onPendingChange?.(row.env, next.trim() !== "");
  };

  const run = async (what: "test" | "save") => {
    setBusy(what);
    setResult(null);
    try {
      const r =
        what === "test"
          ? await postSetupKeyValidate(row.env, value)
          : await postSetupKey(row.env, value);
      if (!alive.current) return;
      setResult(r);
      if (what === "save" && keyOutcomeOk(r)) {
        change(""); // the raw key never lingers in component state
        onSaved(r);
      }
    } catch {
      if (alive.current) {
        setResult({
          env: row.env,
          ok: false,
          kind: "network",
          message: "Couldn't reach Baby's backend. Try again.",
        });
      }
    } finally {
      if (alive.current) setBusy(null);
    }
  };

  const hint = keyHint(row, value);
  const tone = keyTone(result);
  // "<vendor> key works." after a Test reads as done. Say what it did not do.
  const unsaved = keyTestedNotSaved(result, value.trim() !== "");
  const saved = keySavedHint(result);

  return (
    <div className={`wizard-key${row.required ? " required" : ""}`}>
      <div className="wizard-key-head">
        <span className="wizard-key-label">{row.label}</span>
        <span className="wizard-key-state">{keyRowSummary(row)}</span>
      </div>
      <p className="wizard-key-note">{row.note}</p>
      <div className="wizard-key-row">
        <input
          type="password"
          className="wizard-key-input"
          autoComplete="off"
          spellCheck={false}
          placeholder={row.present ? "Replace this key…" : `Paste your ${row.label} key`}
          value={value}
          onChange={(e) => change(e.target.value)}
          aria-label={`${row.label} API key`}
        />
        <button
          type="button"
          className="wizard-key-btn"
          disabled={!value.trim() || busy !== null}
          onClick={() => run("test")}
        >
          {busy === "test" ? "Testing…" : "Test"}
        </button>
        <button
          type="button"
          className="wizard-key-btn primary"
          disabled={!value.trim() || busy !== null}
          onClick={() => run("save")}
        >
          {busy === "save" ? "Saving…" : "Save"}
        </button>
      </div>
      {hint && !result && <p className="wizard-key-hint">{hint}</p>}
      {result && <p className={`wizard-key-msg tone-${tone}`}>{result.message}</p>}
      {unsaved && (
        <p className="wizard-key-msg tone-warn">Not saved yet — press Save to store it.</p>
      )}
      {saved && <p className="wizard-key-hint">{saved}</p>}
      <a
        className="wizard-key-link"
        href={row.signup_url}
        target="_blank"
        rel="noreferrer noopener"
      >
        Get a {row.label} key
      </a>
    </div>
  );
}
