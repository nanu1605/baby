/**
 * Typed fetch wrappers over the existing FastAPI routes (frozen ground — B2
 * reuses every endpoint verbatim). Same-origin in production (dist served by
 * FastAPI); proxied to :8765 in dev (vite.config.ts).
 */
import type { ChatMessage, GraphData, MemoryFact, Stats } from "../types";

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

export const getMemory = (limit = 200) =>
  getJSON<MemoryFact[]>(`/memory?limit=${limit}`);

export const deleteFact = (id: number) =>
  fetch(`/memory/fact/${id}`, { method: "DELETE" });

export const wipeMemory = (phrase: string) => postJSON("/memory/wipe", { phrase });

/** Stub for B5's omnibox — endpoint exists (B1), unused in B2. */
export const search = (q: string) =>
  getJSON<unknown>(`/api/search?q=${encodeURIComponent(q)}`);
