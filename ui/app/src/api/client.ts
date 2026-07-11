/**
 * Typed fetch wrappers over the existing FastAPI routes (frozen ground — B2
 * reuses every endpoint verbatim). Same-origin in production (dist served by
 * FastAPI); proxied to :8765 in dev (vite.config.ts).
 */
import type {
  ChatMessage,
  ConversationDetail,
  ConversationList,
  GraphData,
  MemoryFact,
  NodeStats,
  SearchResponse,
  Stats,
} from "../types";

async function getJSON<T>(url: string): Promise<T> {
  const r = await fetch(url);
  if (!r.ok) throw new Error(`${url} → ${r.status}`);
  return (await r.json()) as T;
}

async function postJSON(url: string, body?: unknown): Promise<Response> {
  return fetch(url, {
    method: "POST",
    headers: body === undefined ? {} : { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
}

export const getStats = () => getJSON<Stats>("/stats");

export const getGraph = () => getJSON<GraphData>("/api/graph");

/** /history → [{role, content}]. Mapped to ChatMessage in the store. */
export const getHistory = () =>
  getJSON<{ role: string; content: string }[]>("/history").then((rows) =>
    rows.map<ChatMessage>((r) => ({
      role: r.role === "user" ? "user" : "assistant",
      text: r.content,
    })),
  );

export const postConfirm = (id: string, approved: boolean) =>
  postJSON(`/confirm/${id}`, { approved });

export const postKill = () => postJSON("/kill");

export const postGameMode = (on: boolean) => postJSON("/game_mode", { on });

// -- v5 chat history ---------------------------------------------------------

/** History sidebar list: real conversations + the live conversation id. */
export const getConversations = (opts?: { includeArchived?: boolean }) =>
  getJSON<ConversationList>(
    `/api/conversations${opts?.includeArchived ? "?include_archived=true" : ""}`,
  );

/** One conversation's meta + messages (read-only viewer). */
export const getConversation = (id: number) =>
  getJSON<ConversationDetail>(`/api/conversations/${id}`);

/** Start a fresh UI conversation; archives the current one by abandoning it. */
export const newConversation = () => postJSON("/conversation/new");

/** Continue a past conversation in the live session (409 if a turn is running). */
export const resumeConversation = (id: number) =>
  postJSON(`/api/conversations/${id}/resume`);

export const getMemory = (limit = 200) =>
  getJSON<MemoryFact[]>(`/memory?limit=${limit}`);

export const deleteFact = (id: number) =>
  fetch(`/memory/fact/${id}`, { method: "DELETE" });

export const wipeMemory = (phrase: string) => postJSON("/memory/wipe", { phrase });

/** B5 omnibox — grouped FTS/vector results (B1 backend stamps each anchor node_id). */
export const search = (q: string) =>
  getJSON<SearchResponse>(`/api/search?q=${encodeURIComponent(q)}`);

// -- B4 node inspector + controls --------------------------------------------

/** Live per-node stats. `id` may contain a colon (tool:x / brain:y) — a valid
 *  path segment, sent as-is. */
export const getNodeStats = (id: string) =>
  getJSON<NodeStats>(`/api/nodes/${id}/stats`);

export const setToolFlag = (name: string, enabled: boolean) =>
  postJSON(`/api/tools/${name}/flag`, { enabled });

export const setBoost = (on: boolean) => postJSON("/api/brain/boost", { on });

export const cancelTask = (id: number) => postJSON(`/api/tasks/${id}/cancel`);

export const runSchedule = (jobId: string) =>
  postJSON(`/api/scheduler/${jobId}/run`);
