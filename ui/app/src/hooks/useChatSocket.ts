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
import { emitActions } from "../graph/pulseBus";
import { eventToActions, remapBrainId } from "../graph/edgeMap";
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
      const brain = (msg.brain as Brain) ?? undefined;
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
            brain,
            tokens: (msg.tokens as Tokens) ?? undefined,
          });
          // The authoritative authoring brain → live recolor (backstop→cloud).
          b.setActiveBrain(remapBrainId(brain?.tier) ?? null);
          // The one-turn boost is consumed server-side; clear its chip.
          if (useBrain.getState().boostArmed) useBrain.setState({ boostArmed: false });
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
      // Honest edge pulses derived from this frame's own signals.
      emitActions(
        eventToActions({
          kind: String(msg.type),
          channel: typeof msg.channel === "string" ? msg.channel : "ui",
          source: typeof msg.source === "string" ? msg.source : undefined,
          target: typeof msg.target === "string" ? msg.target : undefined,
          status: typeof msg.status === "string" ? msg.status : undefined,
          brainTier: brain?.tier,
        }),
      );
    }, (up) => {
      // On a dropped chat socket, close any mid-stream bubble so the cursor stops.
      const b = useBrain.getState();
      b.setWsStatus("chat", up);
      if (!up) b.interruptTurn();
    });

    _send = (text: string) => sock.send({ type: "user_message", text });
    return () => {
      _send = null;
      sock.close();
    };
  }, []);
}
