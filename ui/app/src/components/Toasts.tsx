import { useEffect } from "react";
import { useBrain } from "../store";
import type { Toast } from "../types";

/** Transient notice host (memory wiped / kill / errors). Auto-dismiss at 4s. */
export default function Toasts() {
  const toasts = useBrain((s) => s.toasts);
  return (
    <div className="toasts">
      {toasts.map((t) => (
        <ToastItem key={t.id} t={t} />
      ))}
    </div>
  );
}

function ToastItem({ t }: { t: Toast }) {
  useEffect(() => {
    const id = setTimeout(() => useBrain.getState().dismissToast(t.id), 4000);
    return () => clearTimeout(id);
  }, [t.id]);
  return (
    <div
      className={`toast ${t.kind}`}
      onClick={() => useBrain.getState().dismissToast(t.id)}
    >
      {t.text}
    </div>
  );
}
