"""The chat history sidebar has to react to chatting.

Reported against the 6.0.2 candidate: "when I chat with Baby its summary should be
shown in the chat panel, but currently it shows when I check the Show Archived box."

The API was innocent -- measured against the running install, `/api/conversations`
and `/api/conversations?include_archived=true` returned the same four rows, none of
them archived. The sidebar refreshed on mount, when the archived filter flipped, and
when the active conversation id changed. Chatting in the conversation you are already
in changes none of those, so a row's title, message count and timestamp went stale and
a brand-new chat never appeared at all. Ticking the checkbox changed the refresh
callback's identity, which re-ran the effect -- which is why the filter looked like
the thing revealing the chat when it was only the thing reloading the list.

Two more defects sat behind it. The live conversation id rides on the `turn_start`
frame (`core/bus.py`) and the socket handler dropped it, so the store could only learn
the id FROM the refresh it was supposed to trigger. And clicking a chat dropped into a
read-only viewer rather than opening it, so getting back into a conversation took two
clicks and the first one looked like it had failed.

These are source-shape assertions. This repo has no DOM tests by design
(`ui/app/vitest.config.ts` says so) and `tsc` cannot model any of this, so a gate that
reads the source is the only automated thing standing between these three and a
second report. Each is written against the PATTERN and carries a companion test
proving it fails on the shape that actually shipped -- a source gate that cannot fail
is decoration.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
_SRC = _ROOT / "ui" / "app" / "src"
_SIDEBAR = _SRC / "components" / "HistorySidebar.tsx"
_SOCKET = _SRC / "hooks" / "useChatSocket.ts"
_STORE = _SRC / "store.ts"

# The store field the sidebar keys its refresh off. Named once here so a rename has
# to come through this file rather than silently defeating every gate below.
_TURN_COUNTER = "turnsCompleted"


def _effects(src: str) -> list[tuple[str, str]]:
    """Every `useEffect` in a source file, as (body, dependency-array) pairs.

    Split on the call rather than parsed, because the only thing being asked is what
    a body mentions and what its deps list -- and a real parser is a dependency this
    repo does not carry for one assertion.
    """
    out: list[tuple[str, str]] = []
    for chunk in src.split("useEffect(")[1:]:
        m = re.search(r"\}, \[([^\]]*)\]\s*\)", chunk)
        if m:
            out.append((chunk[: m.start()], m.group(1)))
    return out


def _refresh_effect(src: str) -> tuple[str, str]:
    """The effect that reloads the conversation list."""
    hits = [e for e in _effects(src) if re.search(r"\brefresh\(\)", e[0])]
    assert hits, "no effect reloads the conversation list at all"
    assert len(hits) == 1, "more than one effect reloads the list; which one is live?"
    return hits[0]


def _turn_start_case(src: str) -> str:
    """The socket handler's `turn_start` branch."""
    m = re.search(r'case "turn_start":(.*?)\n\s*case "', src, re.S)
    assert m, "the socket no longer handles turn_start"
    return m.group(1)


@pytest.fixture
def sidebar() -> str:
    return _SIDEBAR.read_text(encoding="utf-8")


@pytest.fixture
def socket() -> str:
    return _SOCKET.read_text(encoding="utf-8")


# -- the list reacts to a finished turn ----------------------------------------


def test_the_list_reloads_when_a_turn_finishes(sidebar: str):
    _body, deps = _refresh_effect(sidebar)
    assert _TURN_COUNTER in deps, (
        "the conversation list only reloads on mount, on the archived filter, and on "
        "an id change -- so chatting leaves it stale, exactly as reported"
    )


def test_the_sidebar_subscribes_to_the_counter_it_depends_on(sidebar: str):
    """A dependency on a value the component never reads is a dependency on nothing:
    it would be captured once and never change."""
    assert re.search(rf"useBrain\(\(s\) => s\.{_TURN_COUNTER}\)", sidebar), (
        "the refresh effect names a counter the component does not subscribe to"
    )


def _finish_turn_body() -> str:
    """The finishTurn REDUCER, not the interface line that declares its signature."""
    store = _STORE.read_text(encoding="utf-8")
    m = re.search(r"finishTurn: \(\{(.*?)\n  addSystemNote:", store, re.S)
    assert m, "finishTurn's implementation is not where it was"
    return m.group(1)


def test_the_store_actually_counts_finished_turns():
    store = _STORE.read_text(encoding="utf-8")
    assert f"{_TURN_COUNTER}: number;" in store, "the counter is not part of the state"
    assert _TURN_COUNTER in _finish_turn_body(), (
        "nothing increments the counter when a turn ends"
    )


def test_the_counter_is_not_silenced_by_the_past_chat_guard():
    """finishTurn returns early while a past chat is being VIEWED, to protect that
    frozen transcript. The conversation LIST is a different thing: a turn landing
    while you read an old chat still changed the live conversation. Counting after
    the guard would put the staleness straight back for anyone who clicks a chat
    mid-answer."""
    body = _finish_turn_body()
    bump = body.index(_TURN_COUNTER)
    guard = body.index("viewingConversationId")
    assert bump < guard, "the counter is incremented behind the viewing guard"


def test_the_gate_can_see_the_staleness_it_was_written_for():
    """The effect exactly as it shipped."""
    shipped = """
  useEffect(() => {
    refresh();
  }, [refresh, activeId]);
"""
    _body, deps = _refresh_effect(shipped)
    assert _TURN_COUNTER not in deps


# -- the live conversation id reaches the store --------------------------------


def test_the_socket_adopts_the_conversation_id(socket: str):
    branch = _turn_start_case(socket)
    assert "conversation_id" in branch, (
        "turn_start carries the live conversation id and the client drops it, so the "
        "store can only learn it from the refresh it is supposed to trigger"
    )
    assert "setActiveConversationId" in branch, (
        "the id is read but never stored, which is the same as dropping it"
    )


def test_the_adopted_id_is_type_checked(socket: str):
    """A frame without the field would otherwise set the active id to undefined and
    blank the sidebar highlight."""
    branch = _turn_start_case(socket)
    assert re.search(r'typeof msg\.conversation_id === "number"', branch), (
        "an absent or non-numeric id is stored as-is"
    )


def test_the_gate_can_see_the_dropped_id():
    """The branch exactly as it shipped."""
    shipped = '''
      switch (msg.type) {
        case "turn_start":
          b.startTurn();
          break;
        case "token":
          b.appendToken(String(msg.text ?? ""));
          break;
'''
    assert "conversation_id" not in _turn_start_case(shipped)


# -- clicking a chat opens it --------------------------------------------------


def test_clicking_a_chat_resumes_it(sidebar: str):
    m = re.search(r"const open = [^;]*?\{(.*?)\n  \};", sidebar, re.S)
    assert m, "the row's click handler is not where it was"
    body = m.group(1)
    assert "resumeConversationLive" in body, (
        "clicking a chat still only opens a read-only viewer, so getting back into a "
        "conversation takes two clicks and the first one looks like it failed"
    )
    assert body.index("resumeConversationLive") < body.index("openConversationView"), (
        "the viewer is tried first, so resume is the fallback rather than the default"
    )


def test_the_read_only_viewer_is_kept_as_the_refusal_path(sidebar: str):
    """Resume is refused with a 409 while a turn is running -- reassigning the
    agent's conversation mid-turn would rehydrate the wrong context. Showing the
    chat read-only is the honest answer there; doing nothing is not."""
    m = re.search(r"const open = [^;]*?\{(.*?)\n  \};", sidebar, re.S)
    body = m.group(1)
    assert "openConversationView" in body, "a refused resume now does nothing at all"
    assert "pushToast" in body, "a refused resume is silent"


def test_search_results_still_only_peek(sidebar: str):
    """The omnibox opens a conversation read-only on purpose: a search hit is
    something you look at, not something you switch the live session to."""
    omnibox = (_SRC / "components" / "Omnibox.tsx").read_text(encoding="utf-8")
    assert "openConversationView" in omnibox
    assert "resumeConversationLive" not in omnibox
