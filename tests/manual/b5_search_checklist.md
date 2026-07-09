# B5 manual acceptance — "Search the brain…" omnibox

Owner-run. B5 adds a top-center omnibox that queries the B1 search backend
(`GET /api/search`) and shows grouped results (Facts · Conversations · Activity ·
Tasks). Selecting a result flies the camera to its anchor node and opens that
node's inspector, reusing the B4 focus cascade (`selectNode` → fly-to + `#node/<id>`
hash + drawer). **Backend is unchanged in B5** — pure frontend. Automated coverage:
`ui/app/src/lib/searchResults.test.ts` (grouping/flatten/select-map/recents), plus
`ui/app` build + full vitest suite.

Prereq: `config.yaml` → `ui.frontend: v3`; `npm --prefix ui/app run build`;
`uv run python run.py --ui`; open `http://127.0.0.1:8765/`. Have some history: a few
past conversations, a couple of remembered facts, some tool activity, and at least
one task (ask Baby to do something long) so every group can return hits.

---

## §1 — Omnibox presence + invocation

- [ ] A search box sits **top-center of the graph**, placeholder exactly
  **`Search the brain...`**.
- [ ] Press **Ctrl/⌘-K** from anywhere → the omnibox focuses and selects its text.
- [ ] Press **`/`** while not typing in another field → the omnibox focuses. (Typing
  `/` while already in the chat input or the omnibox inserts a literal `/`, not a hijack.)
- [ ] Focused with an empty box → a hint (and any recent searches) shows; no results.

## §2 — Grouped results

- [ ] Type a topic you've discussed → results appear **grouped**: Facts,
  Conversations, Activity, Tasks — each with a header, icon, snippet, and (except
  facts) a relative timestamp. Group order is always Facts → Conversations →
  Activity → Tasks.
- [ ] A tool name (e.g. `web_search`) surfaces **Activity** rows for that tool.
- [ ] A past task title surfaces a **Tasks** row.
- [ ] A nonsense string → "No matches for …" empty state.

## §3 — Keyboard-only flow (end to end)

- [ ] Ctrl-K → type a query → **↓/↑** move the highlight across all groups (wraps at
  the ends) → **Enter** selects the highlighted row.
- [ ] Selecting **flies the camera** to the anchor node and **opens its inspector
  drawer**; the URL hash becomes `#node/<id>`.
- [ ] **Esc** with a query clears it; **Esc** on an empty box blurs the omnibox.

## §4 — Per-type fly-to (honest anchors)

- [ ] **Fact** result → flies to `mem_facts`, opens the memory drawer, and the matched
  fact row is **highlighted + scrolled into view** (when it's in the browse list).
- [ ] **Conversation** result → flies to `mem_rag` **and** the right panel switches to
  the **Chat** tab. (It does not fake-reload that past conversation — the snippet in the
  result is the surfaced content.)
- [ ] **Activity** result → focuses that **`tool:<name>`** node's drawer.
- [ ] **Task** result → focuses the **`task_queue`** drawer.
- [ ] Mouse: hovering a row highlights it; clicking selects it (same as Enter).

## §5 — Recent searches + persistence

- [ ] After a few searches, focusing the empty box lists **recent searches**
  (newest first, deduped). Clicking one re-runs it.
- [ ] Recents **survive a page reload** (localStorage `baby.recentSearches`).

## §6 — Honest edge cases

- [ ] Searching a fact that is **not in the first browse page** → the drawer still
  opens on `mem_facts`, just with **no highlight** (nothing faked).
- [ ] (If reproducible) an audit row for a tool no longer registered → selecting it
  shows a **"node no longer in the graph"** toast instead of an empty drawer.

## §7 — /classic + perf

- [ ] `http://127.0.0.1:8765/classic` still works (B5 is additive frontend only).
- [ ] With the omnibox open over an idle graph, sustained GPU/CPU stays within the
  §2 budget (< ~10% GPU / ~5% CPU with the 9B loaded) — the render clock is unchanged
  from B3.

## §8 — Gates

- [ ] `npm --prefix ui/app run build` + `npm --prefix ui/app test` green
  (incl. `searchResults.test.ts`).
- [ ] `uv run pytest -q` green; `uv run pytest tests/test_safety.py -q` green;
  `uv run ruff check .` clean (backend untouched by B5).
- [ ] `git branch --show-current` = `feature/v3-brain-ui`. Zero commits on master.

---

**Restore after:** nothing to undo (read-only feature). Recents can be cleared by
searching new terms or clearing site data.
