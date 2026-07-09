/**
 * /ws/chat — streaming chat. Folds turn_start / token / turn_end / busy / error
 * frames into the store's chat reducers, and backfills /history on mount.
 *
 * The active `send` is stashed module-side so ChatInput can post a user_message
 * without prop-drilling; it is cleared on unmount (lifecycle-managed, not a
 * leaking global singleton).
 */
import { useEffect } from "react";
import { openSocket } from "../api/socket";
import { getHistory } from "../api/client";
import { useBrain } from "../store";
import type { Brain, Tokens } from "../types";

let _send: ((text: string) => void) | null = null;

export function sendUserMessage(text: string): void {
  _send?.(text);
}

export function useChatSocket(): void {
  useEffect(() => {
    getHistory()
      .then((rows) => useBrain.getState().loadHistory(rows))
      .catch(() => {});

    const sock = openSocket("/ws/chat", (msg) => {
      const b = useBrain.getState();
      switch (msg.type) {
        case "turn_start":
          b.startTurn();
          break;
        case "token":
          b.appendToken(String(msg.text ?? ""));
          break;
        case "turn_end":
          b.finishTurn({
            reply: typeof msg.reply === "string" ? msg.reply : undefined,
            status: typeof msg.status === "string" ? msg.status : undefined,
            brain: (msg.brain as Brain) ?? undefined,
            tokens: (msg.tokens as Tokens) ?? undefined,
          });
          break;
        case "busy":
          b.addSystemNote(
            "Still working on the previous request — press ■ Stop to cancel it.",
          );
          break;
        case "error": {
          const t = String(msg.text ?? "error");
          b.addSystemNote(t);
          b.pushToast(t, "error");
          break;
        }
      }
    });

    _send = (text: string) => sock.send({ type: "user_message", text });
    return () => {
      _send = null;
      sock.close();
    };
  }, []);
}
