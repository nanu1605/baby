import { useEffect, useRef, useState } from "react";
import { useBrain } from "../store";
import { deleteFact, getMemory, wipeMemory } from "../api/client";
import type { MemoryFact } from "../types";

/**
 * Memory browse/delete/wipe body, shared by the 🧠 dialog and the memory-node
 * inspector drawer. Challenge-gated wipe: the phrase input must equal WIPE.
 * Loads on mount; the parent decides when to mount it.
 *
 * `highlightId` (B5 search fly-to) best-effort highlights + scrolls to a fact
 * when it's present in the loaded list; if it isn't (beyond the browse window),
 * nothing is faked — the row simply isn't there.
 */
export default function MemoryPanel({ highlightId }: { highlightId?: number | null } = {}) {
  const [facts, setFacts] = useState<MemoryFact[]>([]);
  const [phrase, setPhrase] = useState("");
  const rowRefs = useRef<Record<number, HTMLDivElement | null>>({});

  const load = () => getMemory().then(setFacts).catch(() => setFacts([]));
  useEffect(() => {
    load();
  }, []);

  useEffect(() => {
    if (highlightId == null) return;
    rowRefs.current[highlightId]?.scrollIntoView({ block: "center" });
  }, [highlightId, facts]);

  const del = async (id: number) => {
    await deleteFact(id).catch(() => {});
    load();
  };

  const wipe = async () => {
    if (phrase.trim().toUpperCase() !== "WIPE") {
      useBrain.getState().pushToast("Type WIPE exactly to confirm.", "error");
      return;
    }
    const res = await wipeMemory(phrase).catch(() => null);
    if (res && res.ok) {
      useBrain.getState().pushToast("Memory wiped.");
      setPhrase("");
      load();
    } else {
      useBrain.getState().pushToast("Wipe rejected.", "error");
    }
  };

  const active = facts.filter((f) => f.active).length;

  return (
    <>
      <div className="mem-count">
        {active} remembered · {facts.length - active} forgotten
      </div>
      <div className="memory-list">
        {facts.length === 0 && <div className="mem-empty">No memories yet.</div>}
        {facts.map((f) => (
          <div
            key={f.id}
            ref={(el) => {
              rowRefs.current[f.id] = el;
            }}
            className={`mem-row${f.active ? "" : " forgotten"}${
              f.id === highlightId ? " highlight" : ""
            }`}
          >
            <span className="mem-text">{f.text}</span>
            <button className="mem-del" title="Delete permanently" onClick={() => del(f.id)}>
              ✕
            </button>
          </div>
        ))}
      </div>
      <div className="mem-wipe">
        <input
          className="mem-wipe-input"
          value={phrase}
          onChange={(e) => setPhrase(e.target.value)}
          placeholder="Type WIPE to erase ALL memory"
          autoComplete="off"
        />
        <button className="mem-wipe-btn" onClick={wipe}>
          Wipe
        </button>
      </div>
    </>
  );
}
