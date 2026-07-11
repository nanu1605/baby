import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useBrain } from "../store";
import { search } from "../api/client";
import {
  flattenResults,
  GROUP_META,
  pushRecent,
  resultAction,
  totalCount,
} from "../lib/searchResults";
import type { FlatItem } from "../lib/searchResults";
import { openConversationView } from "../lib/historyActions";
import { ICONS } from "../constants";
import type { SearchGroupKey, SearchItem, SearchResponse } from "../types";

const RECENTS_KEY = "baby.recentSearches";
const DEBOUNCE_MS = 180;

const TYPE_GROUP: Record<SearchItem["type"], SearchGroupKey> = {
  fact: "facts",
  conversation: "conversations",
  activity: "activity",
  task: "tasks",
};

/**
 * "Search the brain…" omnibox (B5). Grouped results from GET /api/search (Facts ·
 * Conversations · Activity · Tasks); selecting one calls `selectNode(node_id)`,
 * which reuses the B4 focus cascade — camera fly-to + `#node/<id>` hash + inspector
 * drawer — for free. Honest: it never selects a node absent from the loaded graph,
 * and a fact result highlights its row only when that fact is actually loaded.
 */
export default function Omnibox() {
  const [query, setQuery] = useState("");
  const [resp, setResp] = useState<SearchResponse | null>(null);
  const [active, setActive] = useState(0);
  const [focused, setFocused] = useState(false);
  const [recents, setRecents] = useState<string[]>(loadRecents);

  const inputRef = useRef<HTMLInputElement>(null);
  const latest = useRef(""); // stale-guard: only accept the newest query's response

  const flat = useMemo(() => flattenResults(resp), [resp]);
  const count = totalCount(resp);
  const hasQuery = query.trim().length > 0;

  // Debounced fetch, with a latest-query guard so an out-of-order response for a
  // stale keystroke can never clobber the current results.
  useEffect(() => {
    const q = query.trim();
    latest.current = q;
    if (!q) {
      setResp(null);
      setActive(0);
      return;
    }
    const t = setTimeout(() => {
      search(q)
        .then((r) => {
          if (latest.current !== q) return;
          setResp(r);
          setActive(0);
        })
        .catch(() => {
          if (latest.current === q) setResp(null);
        });
    }, DEBOUNCE_MS);
    return () => clearTimeout(t);
  }, [query]);

  // Global focus hotkey: Ctrl/⌘-K anywhere, or "/" unless already typing.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const meta = e.ctrlKey || e.metaKey;
      const typing = isTypingTarget(document.activeElement);
      if ((meta && (e.key === "k" || e.key === "K")) || (e.key === "/" && !typing)) {
        e.preventDefault();
        inputRef.current?.focus();
        inputRef.current?.select();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  const commitRecent = useCallback(
    (q: string) => {
      const next = pushRecent(recents, q);
      setRecents(next);
      saveRecents(next);
    },
    [recents],
  );

  const choose = useCallback(
    (fi: FlatItem | undefined) => {
      if (!fi) return;
      const { nodeId, tab, focusFact, openConversation } = resultAction(fi.item);
      const st = useBrain.getState();
      // Never select a target that isn't in the loaded graph (a de-registered
      // tool's audit row anchors to a missing tool:<name>). Only block when the
      // graph is loaded AND the node is known-absent.
      const nodes = st.graph?.nodes;
      if (nodes && !nodes.some((n) => n.id === nodeId)) {
        st.pushToast("That node is no longer in the graph.", "error");
        return;
      }
      if (focusFact != null) st.setFocusFact(focusFact);
      st.selectNode(nodeId); // → camera fly-to + #node hash + inspector drawer
      if (tab) st.setTab(tab);
      // v5: a conversation hit opens that conversation read-only in the panel.
      if (openConversation != null) {
        openConversationView(openConversation).catch(() =>
          st.pushToast("Couldn't open that conversation.", "error"),
        );
      }
      commitRecent(query);
      setQuery("");
      setResp(null);
      setFocused(false);
      inputRef.current?.blur();
    },
    [query, commitRecent],
  );

  const onKeyDown = (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (e.key === "ArrowDown") {
      e.preventDefault();
      setActive((i) => (flat.length ? (i + 1) % flat.length : 0));
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      setActive((i) => (flat.length ? (i - 1 + flat.length) % flat.length : 0));
    } else if (e.key === "Enter") {
      e.preventDefault();
      choose(flat[active] ?? flat[0]);
    } else if (e.key === "Escape") {
      e.preventDefault();
      if (hasQuery || resp) {
        setQuery("");
        setResp(null);
      } else {
        inputRef.current?.blur();
      }
    }
  };

  return (
    <div className="omnibox">
      <div className="omni-input-wrap">
        <span className="omni-icon">🔎</span>
        <input
          ref={inputRef}
          className="omni-input"
          value={query}
          placeholder="Search the brain..."
          spellCheck={false}
          autoComplete="off"
          aria-label="Search the brain"
          onChange={(e) => setQuery(e.target.value)}
          onKeyDown={onKeyDown}
          onFocus={() => setFocused(true)}
          onBlur={() => setTimeout(() => setFocused(false), 120)}
        />
        {hasQuery && (
          <button
            className="omni-clear"
            title="clear"
            onMouseDown={(e) => e.preventDefault()}
            onClick={() => {
              setQuery("");
              setResp(null);
              inputRef.current?.focus();
            }}
          >
            ✕
          </button>
        )}
      </div>

      {focused && (
        <div className="omni-results" role="listbox">
          {!hasQuery && recents.length > 0 && (
            <RecentList
              recents={recents}
              onPick={(q) => {
                setQuery(q);
                inputRef.current?.focus();
              }}
            />
          )}
          {!hasQuery && recents.length === 0 && (
            <div className="omni-hint omni-hint-solo">
              Search facts, conversations, activity, and tasks.
            </div>
          )}
          {hasQuery && !resp && <div className="omni-hint omni-hint-solo">Searching…</div>}
          {hasQuery && resp && count === 0 && (
            <div className="omni-empty">No matches for “{query.trim()}”.</div>
          )}
          {hasQuery && count > 0 &&
            flat.map((fi, i) => {
              const prev = flat[i - 1];
              const showHead = !prev || prev.group !== fi.group;
              const meta = GROUP_META[fi.group];
              return (
                <div key={fi.key}>
                  {showHead && (
                    <div
                      className="omni-group-head"
                      style={{ color: `var(${meta.colorVar})` }}
                    >
                      <span>{meta.glyph}</span>
                      <span>{meta.label}</span>
                      <span className="omni-group-count">
                        {resp?.groups[fi.group]?.length ?? 0}
                      </span>
                    </div>
                  )}
                  <Row
                    item={fi.item}
                    activeRow={fi.index === active}
                    onHover={() => setActive(fi.index)}
                    onPick={() => choose(fi)}
                  />
                </div>
              );
            })}
        </div>
      )}
    </div>
  );
}

function Row({
  item,
  activeRow,
  onHover,
  onPick,
}: {
  item: SearchItem;
  activeRow: boolean;
  onHover: () => void;
  onPick: () => void;
}) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (activeRow) ref.current?.scrollIntoView({ block: "nearest" });
  }, [activeRow]);
  return (
    <div
      ref={ref}
      role="option"
      aria-selected={activeRow}
      className={"omni-row" + (activeRow ? " active" : "")}
      onMouseEnter={onHover}
      onMouseDown={(e) => e.preventDefault()} // keep input focus so blur doesn't cancel the click
      onClick={onPick}
    >
      <span className="omni-row-icon">{rowIcon(item)}</span>
      <span className="omni-row-snippet">{item.snippet}</span>
      {item.ts && <span className="omni-row-ts">{formatWhen(item.ts)}</span>}
    </div>
  );
}

function RecentList({
  recents,
  onPick,
}: {
  recents: string[];
  onPick: (q: string) => void;
}) {
  return (
    <div>
      <div className="omni-group-head omni-recent-head">
        <span>🕘</span>
        <span>Recent</span>
      </div>
      {recents.map((q) => (
        <div
          key={q}
          className="omni-row omni-recent"
          onMouseDown={(e) => e.preventDefault()}
          onClick={() => onPick(q)}
        >
          <span className="omni-row-icon">↩</span>
          <span className="omni-row-snippet">{q}</span>
        </div>
      ))}
      <div className="omni-hint">Search facts, conversations, activity, and tasks.</div>
    </div>
  );
}

function rowIcon(item: SearchItem): string {
  if (item.type === "activity") {
    const tool = item.node_id.startsWith("tool:") ? item.node_id.slice("tool:".length) : "";
    return ICONS[tool] ?? GROUP_META.activity.glyph;
  }
  return GROUP_META[TYPE_GROUP[item.type]].glyph;
}

function formatWhen(ts: string): string {
  const d = new Date(ts.replace(" ", "T"));
  if (isNaN(d.getTime())) return "";
  const diff = (Date.now() - d.getTime()) / 1000;
  if (diff < 60) return "just now";
  if (diff < 3600) return `${Math.floor(diff / 60)}m ago`;
  if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`;
  if (diff < 86400 * 7) return `${Math.floor(diff / 86400)}d ago`;
  return d.toLocaleDateString(undefined, { month: "short", day: "numeric" });
}

function isTypingTarget(el: Element | null): boolean {
  if (!el) return false;
  const tag = el.tagName;
  return (
    tag === "INPUT" || tag === "TEXTAREA" || (el as HTMLElement).isContentEditable === true
  );
}

function loadRecents(): string[] {
  try {
    const raw = localStorage.getItem(RECENTS_KEY);
    const arr = raw ? JSON.parse(raw) : [];
    return Array.isArray(arr) ? arr.filter((x) => typeof x === "string") : [];
  } catch {
    return [];
  }
}

function saveRecents(list: string[]): void {
  try {
    localStorage.setItem(RECENTS_KEY, JSON.stringify(list));
  } catch {
    /* private mode / no storage — fine */
  }
}
