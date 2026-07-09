import { useMemo } from "react";
import type { ChatMessage } from "../types";
import { renderMarkdown } from "../lib/markdown";
import { BRAIN_LABELS } from "../constants";

/**
 * One chat bubble. Assistant final replies render as sanitized markdown; user,
 * system, and still-streaming bubbles render as plain text (never innerHTML), so
 * the live token stream can never inject markup.
 */
export default function Message({ m }: { m: ChatMessage }) {
  const finalized = m.role === "assistant" && !m.streaming;
  const html = useMemo(
    () => (finalized ? renderMarkdown(m.text) : null),
    [finalized, m.text],
  );

  const cls =
    `msg ${m.role}` +
    (m.streaming ? " streaming" : "") +
    (m.role === "system" ? " system-note" : "");

  return (
    <div className={cls}>
      {html != null ? (
        <div className="md" dangerouslySetInnerHTML={{ __html: html }} />
      ) : (
        <span className="md-plain">{m.text}</span>
      )}
      {finalized && <Badges m={m} />}
    </div>
  );
}

function Badges({ m }: { m: ChatMessage }) {
  const tier = m.brain?.tier;
  const label = tier ? BRAIN_LABELS[tier] : undefined;
  const t = m.tokens;
  const local = tier === "daily";
  return (
    <span className="badges">
      {label && (
        <span
          className={`brain-badge ${label[1]}`}
          title={`${m.brain?.model ?? ""}${m.brain?.reason ? " — " + m.brain.reason : ""}`}
        >
          {label[0]}
        </span>
      )}
      {t && t.total > 0 && (
        <span
          className={"token-badge" + (local ? " local" : "")}
          title={
            local
              ? `local — no quota (${t.total} tokens)`
              : `${t.total} tokens this turn`
          }
        >
          ↑{t.prompt} ↓{t.completion}
        </span>
      )}
    </span>
  );
}
