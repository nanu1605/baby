"""P4 budget trim: drop oldest whole turns to fit a per-brain history budget,
keeping system/summary/RAG blocks pinned and never splitting a tool pair."""

from __future__ import annotations

from core.context import estimate_tokens, trim

SYS = {"role": "system", "content": "S" * 400}  # ~100 tok, pinned
SUMMARY = {"role": "system", "content": "rolling summary " * 20}  # pinned
TRAIL = {"role": "system", "content": "reply in English"}  # pinned


def _u(n, tag):
    return {"role": "user", "content": f"{tag} " + "x" * (n * 4)}


def _a(n, tag):
    return {"role": "assistant", "content": f"{tag} " + "y" * (n * 4)}


def test_trim_noop_under_budget():
    msgs = [SYS, _u(10, "q1"), _a(10, "a1"), TRAIL]
    assert trim(msgs, 10_000) == msgs


def test_trim_zero_budget_is_noop():
    msgs = [SYS, _u(10, "q1"), _a(10, "a1")]
    assert trim(msgs, 0) == msgs
    assert trim(msgs, None) == msgs


def test_trim_drops_oldest_turns_first():
    # Three ~100-tok exchanges; budget 250 tok keeps only the newest ~2.
    msgs = [SYS, _u(100, "q1"), _a(100, "a1"), _u(100, "q2"), _a(100, "a2"),
            _u(100, "q3"), TRAIL]
    out = trim(msgs, 250)
    texts = " ".join(m["content"] for m in out)
    assert "q1" not in texts and "a1" not in texts  # oldest dropped
    assert "q3" in texts  # newest kept
    # every system message survives wherever it sat
    assert out[0] is SYS and out[-1] is TRAIL


def test_trim_pins_all_system_blocks():
    msgs = [SYS, SUMMARY, _u(200, "old"), _a(200, "old"), _u(50, "new"), TRAIL]
    out = trim(msgs, 100)
    assert SYS in out and SUMMARY in out and TRAIL in out  # never dropped
    assert any("new" in m["content"] for m in out)


def test_trim_keeps_newest_turn_even_if_over_budget():
    # A single huge newest turn can't be split away — it survives whole.
    msgs = [SYS, _u(50, "old"), _a(50, "old"), _u(500, "huge_new")]
    out = trim(msgs, 100)
    assert any("huge_new" in m["content"] for m in out)
    assert SYS in out


def test_trim_keeps_current_user_across_tool_rounds():
    """Review #1 regression: the current user question survives even when it is
    NOT the last block (a trailing system nudge + multi-step tool rounds follow
    it) and the tool outputs alone blow the budget."""
    def _tc(cid):
        return {"role": "assistant", "content": None,
                "tool_calls": [{"id": cid, "function": {"name": "read", "arguments": "{}"}}]}

    def big_tool(cid):
        return {"role": "tool", "tool_call_id": cid, "content": "z" * 4000}

    msgs = [
        SYS,
        _u(80, "old_q"), _a(80, "old_a"),
        _u(20, "CURRENT question please"),
        TRAIL,                       # system nudge sits AFTER the current user
        _tc("c1"), big_tool("c1"),   # ~1000 tok
        _tc("c2"), big_tool("c2"),   # ~1000 tok
    ]
    out = trim(msgs, 600)  # tool rounds alone exceed the budget
    texts = " ".join(str(m.get("content") or "") for m in out)
    assert "CURRENT question please" in texts  # the user's ask is never lost
    assert "old_q" not in texts  # older history dropped instead
    # both tool pairs of the in-flight turn are intact
    assert sum(1 for m in out if m.get("tool_calls")) == 2
    assert sum(1 for m in out if m.get("role") == "tool") == 2


def test_trim_never_splits_tool_call_pair():
    assistant_tc = {
        "role": "assistant",
        "content": None,
        "tool_calls": [{"id": "c1", "function": {"name": "t", "arguments": "{}"}}],
    }
    tool_resp = {"role": "tool", "tool_call_id": "c1", "content": "z" * 400}
    # Old bulky exchange first, then a current tool round that must stay intact.
    msgs = [SYS, _u(200, "old"), _a(200, "old"), _u(20, "cur"), assistant_tc, tool_resp]
    out = trim(msgs, 150)
    has_call = any(m.get("tool_calls") for m in out)
    has_resp = any(m.get("role") == "tool" for m in out)
    assert has_call == has_resp  # both present or both gone — never orphaned
    # here the newest block (the tool round) is kept, so both are present
    assert has_call and has_resp


def test_trim_budget_covers_history_not_system():
    # System blocks are large but exempt; only history counts toward budget.
    big_sys = {"role": "system", "content": "S" * 10_000}
    msgs = [big_sys, _u(10, "q"), _a(10, "a")]
    out = trim(msgs, 5_000)  # history (~20 tok) already under budget
    assert out == msgs  # nothing dropped despite the huge system block


def test_estimate_tokens_counts_content_only():
    assert estimate_tokens([{"role": "user", "content": "abcd"}]) == 1
    assert estimate_tokens([{"role": "assistant", "tool_calls": [], "content": None}]) == 0
