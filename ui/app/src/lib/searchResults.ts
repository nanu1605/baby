/**
 * Pure grouping / ordering / select-mapping for the "Search the brain…" omnibox
 * (B5). The backend (GET /api/search) already stamps each result's anchor
 * `node_id` and exposes no comparable cross-type score (cosine vs bm25), so
 * "ranking" here is purely a fixed group order (Facts → Conversations → Activity
 * → Tasks) with the server's intra-group order preserved.
 *
 * `resultAction` is the single, inspectable signal→target derivation: it reads
 * only the server-stamped `node_id` (never invents an anchor). The omnibox then
 * verifies that anchor exists in the loaded graph before selecting, so a stale
 * result can never open a dangling drawer. Pure → unit-tested.
 */
import type { SearchGroupKey, SearchItem, SearchResponse } from "../types";

export const GROUP_ORDER: SearchGroupKey[] = [
  "facts",
  "conversations",
  "activity",
  "tasks",
];

export interface GroupMeta {
  key: SearchGroupKey;
  label: string;
  glyph: string;
  /** design-token var for the group accent (see styles/tokens.css). */
  colorVar: string;
}

export const GROUP_META: Record<SearchGroupKey, GroupMeta> = {
  facts: { key: "facts", label: "Facts", glyph: "🧠", colorVar: "--node-memory" },
  conversations: {
    key: "conversations",
    label: "Conversations",
    glyph: "💬",
    colorVar: "--node-memory",
  },
  activity: { key: "activity", label: "Activity", glyph: "⚙", colorVar: "--node-tool" },
  tasks: { key: "tasks", label: "Tasks", glyph: "✓", colorVar: "--node-infra" },
};

export interface FlatItem {
  /** stable per-result key = `${group}:${id}`. */
  key: string;
  group: SearchGroupKey;
  item: SearchItem;
  /** position in the flattened keyboard-nav list. */
  index: number;
}

/**
 * Flatten grouped results into one keyboard-nav-ordered list: group order first,
 * then the server's intra-group order. Each entry carries a contiguous nav index.
 */
export function flattenResults(resp: SearchResponse | null): FlatItem[] {
  if (!resp) return [];
  const out: FlatItem[] = [];
  for (const g of GROUP_ORDER) {
    for (const item of resp.groups[g] ?? []) {
      out.push({ key: `${g}:${item.id}`, group: g, item, index: out.length });
    }
  }
  return out;
}

export function totalCount(resp: SearchResponse | null): number {
  if (!resp) return 0;
  return GROUP_ORDER.reduce((n, g) => n + (resp.groups[g]?.length ?? 0), 0);
}

export interface ResultAction {
  nodeId: string;
  tab?: "chat";
  focusFact?: number;
  /** v5: conversation to open read-only in the chat panel (the deep-link target). */
  openConversation?: number;
}

/**
 * Map a result → what selecting it should do. Reads only server-stamped fields
 * (never fabricates an anchor). A conversation switches to the Chat tab AND, when
 * the backend supplied its conversation_id, opens that conversation read-only in
 * the panel (v5 — closes the v3 loop where a conversation hit had nowhere to
 * land); a fact requests a best-effort highlight of that fact id in memory.
 */
export function resultAction(item: SearchItem): ResultAction {
  const action: ResultAction = { nodeId: item.node_id };
  if (item.type === "conversation") {
    action.tab = "chat";
    if (item.conversation_id != null) action.openConversation = item.conversation_id;
  }
  if (item.type === "fact") action.focusFact = item.id;
  return action;
}

/**
 * Recent-search list maintenance for the localStorage-backed dropdown. Newest
 * first, deduped case-insensitively (a repeat moves to the front), capped, blanks
 * ignored. Pure.
 */
export function pushRecent(list: string[], q: string, max = 8): string[] {
  const query = q.trim();
  if (!query) return list;
  const lower = query.toLowerCase();
  const kept = list.filter((x) => x.toLowerCase() !== lower);
  return [query, ...kept].slice(0, max);
}
