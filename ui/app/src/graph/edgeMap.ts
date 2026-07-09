/**
 * Signal → graph-edge derivation map (B3). The load-bearing honesty layer:
 * turns each live event into the particle pulses / node flashes it truthfully
 * implies. Derivation uses ONLY the current event's own signals (no cross-turn
 * correlation). Edges with no honest signal return nothing — never faked.
 *
 * ── Derivation table (mirrored in DECISIONS.md) ────────────────────────────
 * event                         → action(s)                              class
 * turn_start (src baby_core)    → pulse baby_core→router                 normal
 * turn_end   (brain.tier T)     → pulse router→brain:T                   normal
 * status  ch=router, target T   → pulse router→brain:T                   normal
 * tool_start (src brain:T,      → pulse brain:T→safety_gate,             by class
 *             tgt tool:X, class)          safety_gate→tool:X
 * tool_end   status error-ish   → flash tool:X                           error
 * confirm_request               → flash safety_gate                      confirm
 * status  ch=voice "heard …"    → pulse voice_stt→router                 normal
 * token   ch=voice              → pulse baby_core→voice_tts (spoken)     normal
 * token   ch=ui                 → (none — reply shows in chat, no node)
 *
 * DARK (zero honest signal — never pulsed): brain→mem_facts/mem_rag/mem_summaries
 * (memory access never hits the bus); per-stage voice voice_wake / voice_vad
 * (only aggregate "voice:" status text exists). The reply-return edge
 * brain→baby_core does not exist in the topology, so text-turn replies are shown
 * by the core gauge's "speaking" state rather than a faked edge.
 *
 * ⚠ backstop→cloud remap: the router's tier token is `backstop`, but the graph
 * brain node (from the config `cloud` key) is `brain:cloud`. Remap or the edge
 * never matches a real link.
 */
import type { PulseAction, PulseClass } from "./pulseBus";

export interface PulseEvent {
  kind: string;
  channel?: string;
  source?: string;
  target?: string;
  safety_class?: string;
  status?: string;
  text?: string;
  /** turn_end carries the authoritative authoring brain: use its tier. */
  brainTier?: string;
}

const ERROR_STATUS = new Set(["error", "denied", "refused", "timeout"]);

/** Router tier `backstop` ⇒ graph node `brain:cloud` (config key mismatch). */
export function remapBrainId(id: string | undefined): string | undefined {
  if (!id) return id;
  if (id === "brain:backstop") return "brain:cloud";
  if (id === "backstop") return "brain:cloud";
  return id.startsWith("brain:") ? id : `brain:${id}`;
}

function toolClass(safety?: string): PulseClass {
  if (safety === "deny") return "error";
  if (safety === "confirm") return "confirm";
  return "normal";
}

export function eventToActions(ev: PulseEvent): PulseAction[] {
  switch (ev.kind) {
    case "turn_start":
      // The core consults the router at the start of every turn.
      return ev.source === "baby_core"
        ? [{ type: "pulse", from: "baby_core", to: "router", klass: "normal" }]
        : [];

    case "turn_end": {
      // turn_end.brain is the authoritative authoring brain → router picked it.
      const to = remapBrainId(ev.brainTier);
      return to
        ? [{ type: "pulse", from: "router", to, klass: "normal" }]
        : [];
    }

    case "status": {
      if (ev.channel === "router" && ev.target) {
        const to = remapBrainId(ev.target);
        return to
          ? [{ type: "pulse", from: "router", to, klass: "normal" }]
          : [];
      }
      if (ev.channel === "voice" && (ev.text || "").includes("heard")) {
        // STT produced text → it flows into the router (the one honest voice edge).
        return [{ type: "pulse", from: "voice_stt", to: "router", klass: "normal" }];
      }
      return [];
    }

    case "tool_start": {
      const from = remapBrainId(ev.source);
      const to = ev.target; // tool:<name>
      if (!from || !to) return [];
      const klass = toolClass(ev.safety_class);
      // Real path is 2 hops through the gate.
      return [
        { type: "pulse", from, to: "safety_gate", klass },
        { type: "pulse", from: "safety_gate", to, klass },
      ];
    }

    case "tool_end":
      // The pulse already fired on tool_start; on failure, flash the tool node.
      return ev.target && ev.status && ERROR_STATUS.has(ev.status)
        ? [{ type: "flash", node: ev.target, klass: "error" }]
        : [];

    case "confirm_request":
      return [{ type: "flash", node: "safety_gate", klass: "confirm" }];

    case "token":
      // Voice replies flow out through TTS; text replies have no graph node.
      return ev.channel === "voice"
        ? [{ type: "pulse", from: "baby_core", to: "voice_tts", klass: "normal" }]
        : [];

    default:
      return [];
  }
}
