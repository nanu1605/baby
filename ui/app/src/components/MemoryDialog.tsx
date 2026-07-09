import { useEffect, useRef } from "react";
import { useBrain } from "../store";
import MemoryPanel from "./MemoryPanel";

/**
 * The 🧠 memory dialog — a thin <dialog> wrapper around the shared MemoryPanel
 * (browse / delete / challenge-gated wipe). The same panel powers the memory-node
 * inspector drawer.
 */
export default function MemoryDialog() {
  const open = useBrain((s) => s.memoryOpen);
  const ref = useRef<HTMLDialogElement>(null);

  useEffect(() => {
    const d = ref.current;
    if (!d) return;
    if (open) {
      if (!d.open) d.showModal();
    } else if (d.open) {
      d.close();
    }
  }, [open]);

  const close = () => useBrain.getState().closeMemory();

  return (
    <dialog ref={ref} className="memory-dialog" onCancel={close}>
      <div className="mem-head">
        <h3>Memory</h3>
        <button className="mem-close" onClick={close}>
          ✕
        </button>
      </div>
      {open && <MemoryPanel />}
    </dialog>
  );
}
