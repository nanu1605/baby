/**
 * Filter the live-event ring to a single node's recent activity (B4 inspector).
 * A node's events are those whose `source`/`target` is the node id, plus, for a
 * tool node, events whose `payload.tool` is the tool name (tool_start/tool_end
 * carry both). Pure → unit-tested.
 */
import type { LiveEvent } from "../types";

export function nodeEvents(events: LiveEvent[], nodeId: string, limit = 30): LiveEvent[] {
  const toolName = nodeId.startsWith("tool:") ? nodeId.slice("tool:".length) : null;
  const out = events.filter((e) => {
    if (e.source === nodeId || e.target === nodeId) return true;
    if (toolName && (e.payload as { tool?: unknown }).tool === toolName) return true;
    return false;
  });
  return out.slice(-limit);
}
