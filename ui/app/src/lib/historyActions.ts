/**
 * v5 chat-history orchestration — the shared side-effectful flows the history
 * sidebar and the search omnibox both drive. Each composes the typed client
 * calls with the store setters so "open a past chat", "resume", "new chat", and
 * "return to live" behave identically wherever they're triggered.
 *
 * None of these emit turn_start / token, so switching chats never fires a
 * phantom brain pulse (honest-data invariant): the transcript is replaced via
 * setTranscript, never appendToken.
 */
import { useBrain } from "../store";
import {
  getConversation,
  getHistory,
  newConversation,
  resumeConversation,
} from "../api/client";
import type { ChatMessage, ConversationDetail } from "../types";

function toChat(rows: ConversationDetail["messages"]): ChatMessage[] {
  return rows.map((r) => ({
    role: r.role === "user" ? "user" : "assistant",
    text: r.content,
  }));
}

/** Open a past conversation read-only in the chat panel (view-only). */
export async function openConversationView(id: number): Promise<void> {
  const detail = await getConversation(id);
  const b = useBrain.getState();
  b.setTranscript(toChat(detail.messages));
  b.setViewing(id);
  b.setTab("chat"); // make sure the panel showing the transcript is visible
}

/** Leave the viewer and re-show the live conversation (re-fetched from /history). */
export async function returnToLive(): Promise<void> {
  const rows = await getHistory();
  const b = useBrain.getState();
  b.setViewing(null);
  b.setTranscript(rows);
}

/** Start a fresh conversation; the previous one drops into history. */
export async function startNewChat(): Promise<number | null> {
  const r = await newConversation();
  const data = (await r.json().catch(() => ({}))) as { conversation_id?: number };
  const b = useBrain.getState();
  b.setViewing(null);
  b.setTranscript([]);
  if (typeof data.conversation_id === "number") {
    b.setActiveConversationId(data.conversation_id);
  }
  return data.conversation_id ?? null;
}

/**
 * Continue a past conversation in the live session. Returns false when the
 * backend refuses (409 turn-in-progress / 404 missing) so the caller can toast.
 */
export async function resumeConversationLive(id: number): Promise<boolean> {
  const r = await resumeConversation(id);
  if (!r.ok) return false;
  const detail = await getConversation(id);
  const b = useBrain.getState();
  b.setViewing(null);
  b.setTranscript(toChat(detail.messages));
  b.setActiveConversationId(id);
  return true;
}
