/**
 * Reconnecting WebSocket — a TS port of the classic `reconnectingSocket`
 * (ui/web/app.js:7-21). Same backoff (500ms → ×2 → 8000ms cap, reset on open),
 * infinite retry, and `send()` drops when not OPEN. Improvement over the classic
 * version: `wss:`-aware scheme so it survives behind TLS (e.g. Tailscale).
 *
 * B7: an optional `onStatus(up)` callback surfaces connection state to consumers
 * (open → true, close/error → false) so the UI can show a reconnecting state and
 * finalize a stuck stream instead of silently freezing. Backoff/retry unchanged;
 * existing two-arg callers are unaffected.
 */
import type { WSFrame } from "../types";

export interface Socket {
  send: (obj: unknown) => void;
  close: () => void;
}

export function openSocket(
  path: string,
  onMessage: (msg: WSFrame) => void,
  onStatus?: (up: boolean) => void,
): Socket {
  let ws: WebSocket | null = null;
  let delay = 500;
  let closed = false;

  const scheme = location.protocol === "https:" ? "wss:" : "ws:";
  const url = `${scheme}//${location.host}${path}`;

  function connect() {
    if (closed) return;
    ws = new WebSocket(url);
    ws.onopen = () => {
      delay = 500;
      onStatus?.(true);
    };
    ws.onmessage = (e) => {
      try {
        onMessage(JSON.parse(e.data));
      } catch {
        /* malformed frame — ignore */
      }
    };
    // A failed/dropped connection always ends in onclose; onerror is a redundant
    // early signal. Both are no-ops once we've intentionally closed (unmount).
    ws.onerror = () => {
      if (!closed) onStatus?.(false);
    };
    ws.onclose = () => {
      if (closed) return;
      onStatus?.(false);
      setTimeout(connect, delay);
      delay = Math.min(delay * 2, 8000);
    };
  }
  connect();

  return {
    send: (obj) => {
      if (ws && ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify(obj));
    },
    close: () => {
      closed = true;
      ws?.close();
    },
  };
}
