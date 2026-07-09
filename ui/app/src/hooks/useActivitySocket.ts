/**
 * /ws/activity — the full event stream (all channels). Every frame goes into the
 * live-event ring buffer (the graph pulses off this in B3; the Activity panel
 * renders it now). Confirmation requests open the modal; a resolution closes it;
 * meaningful status lines raise a toast.
 */
import { useEffect } from "react";
import { openSocket } from "../api/socket";
import { nextEventSeq, useBrain } from "../store";
import type { LiveEvent, WSFrame } from "../types";

// Status lines worth a transient toast (avoid noisy voice "listening" spam).
const TOAST_STATUS = /wiped|kill|error|cancelled|offline|degraded/i;

function toLiveEvent(msg: WSFrame): LiveEvent {
  const { type, ts, channel, source, target, turn_id, ...payload } = msg;
  return {
    seq: nextEventSeq(),
    kind: type,
    channel: typeof channel === "string" ? channel : "",
    ts: typeof ts === "string" ? ts : "",
    source: typeof source === "string" ? source : undefined,
    target: typeof target === "string" ? target : undefined,
    turnId: typeof turn_id === "number" ? turn_id : undefined,
    payload,
  };
}

export function useActivitySocket(): void {
  useEffect(() => {
    const sock = openSocket("/ws/activity", (msg) => {
      const b = useBrain.getState();
      b.pushEvent(toLiveEvent(msg));

      if (msg.type === "confirm_request") {
        b.openConfirm({
          confirm_id: String(msg.confirm_id ?? ""),
          tool: typeof msg.tool === "string" ? msg.tool : undefined,
          command: String(msg.command ?? ""),
          explanation: String(msg.explanation ?? ""),
          timeout_s: typeof msg.timeout_s === "number" ? msg.timeout_s : 60,
        });
      } else if (msg.type === "confirm_resolved") {
        b.clearConfirm(
          typeof msg.confirm_id === "string" ? msg.confirm_id : undefined,
        );
      } else if (msg.type === "status") {
        const t = String(msg.text ?? "");
        if (TOAST_STATUS.test(t)) b.pushToast(t);
      }
    });
    return () => sock.close();
  }, []);
}
