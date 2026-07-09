import { useEffect, useRef, useState } from "react";
import { useBrain } from "../store";
import { postConfirm } from "../api/client";

/**
 * Safety-gate confirmation (gate parity). A confirm_request opens the native
 * <dialog>; Approve/Deny POST /confirm/{id}; Esc denies; a matching
 * confirm_resolved (server timeout / kill) auto-closes it.
 */
export default function ConfirmModal() {
  const confirm = useBrain((s) => s.activeConfirm);
  const ref = useRef<HTMLDialogElement>(null);
  const [left, setLeft] = useState(0);

  useEffect(() => {
    const d = ref.current;
    if (!d) return;
    if (confirm) {
      if (!d.open) d.showModal();
      setLeft(Math.floor(confirm.timeout_s));
    } else if (d.open) {
      d.close();
    }
  }, [confirm]);

  useEffect(() => {
    if (!confirm) return;
    const id = setInterval(() => setLeft((x) => Math.max(0, x - 1)), 1000);
    return () => clearInterval(id);
  }, [confirm]);

  const answer = async (approved: boolean) => {
    const c = useBrain.getState().activeConfirm;
    if (c) await postConfirm(c.confirm_id, approved).catch(() => {});
    useBrain.getState().clearConfirm();
  };

  return (
    <dialog
      ref={ref}
      className="confirm-dialog"
      onCancel={(e) => {
        e.preventDefault();
        answer(false);
      }}
    >
      {confirm && (
        <>
          <h3>Confirm action</h3>
          <pre className="confirm-command">{confirm.command}</pre>
          <p className="confirm-explanation">{confirm.explanation}</p>
          <p className="countdown">auto-deny in {left}s</p>
          <div className="dialog-buttons">
            <button className="approve" onClick={() => answer(true)}>
              Approve
            </button>
            <button className="deny" onClick={() => answer(false)}>
              Deny
            </button>
          </div>
        </>
      )}
    </dialog>
  );
}
