"""P0 repro (#7): the context builder must emit an OpenAI-valid sequence.

Failed/interrupted turns leave debris in baby.db — orphaned tool results, tool
rows with no preceding assistant tool_calls, malformed JSON args, empty-content
rows. Today core/agent.py replays that verbatim (agent.py:170-191, no
sanitization) and strict providers reject it. P2 adds
core/context.py::sanitize_messages as a strict gate that guarantees an
OpenAI-valid message array regardless of what the DB holds.

These fixtures define "clean". They fail today (module core.context does not
exist) and go green once P2 lands. Either repair strategy is allowed — fix
malformed args in place, or drop the whole call/answer pair — as long as the
emitted sequence is valid.
"""

from __future__ import annotations

import json


def _assert_openai_valid(messages: list[dict]) -> None:
    prev: dict | None = None
    for i, m in enumerate(messages):
        role = m.get("role")
        assert role in {"system", "user", "assistant", "tool"}, f"bad role at {i}: {role}"
        content = m.get("content")
        tool_calls = m.get("tool_calls")
        # No empty-content rows, unless it is an assistant carrying tool_calls.
        if not (role == "assistant" and tool_calls):
            assert content is not None and str(content).strip(), f"empty content at {i}: {m}"
        if role == "assistant" and tool_calls:
            for tc in tool_calls:
                json.loads(tc["function"]["arguments"])  # must be valid JSON
        if role == "tool":
            # A tool row must answer a tool_call from the immediately preceding
            # assistant message.
            assert prev is not None and prev.get("role") == "assistant" and prev.get(
                "tool_calls"
            ), f"orphaned tool row at {i}"
            ids = {tc["id"] for tc in prev["tool_calls"]}
            assert m.get("tool_call_id") in ids, f"tool row {i} matches no preceding call"
        prev = m


def _poisoned() -> list[dict]:
    return [
        {"role": "system", "content": "You are Baby."},
        {"role": "user", "content": "hi"},
        # Orphaned tool result — no preceding assistant tool_calls.
        {"role": "tool", "tool_call_id": "orphan", "content": "stale"},
        {"role": "assistant", "content": "ok"},
        # Empty-content user row (whitespace only).
        {"role": "user", "content": "   "},
        # Assistant with MALFORMED tool-call args, plus its tool answer.
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "c1",
                    "type": "function",
                    "function": {"name": "get_time", "arguments": "{bad json"},
                }
            ],
        },
        {"role": "tool", "tool_call_id": "c1", "content": "12:00"},
        {"role": "user", "content": "thanks"},
    ]


def test_sanitizer_emits_openai_valid_sequence():
    from core.context import sanitize_messages  # built in P2

    clean = sanitize_messages(_poisoned())
    _assert_openai_valid(clean)
    # The orphaned tool row is gone.
    assert not any(m.get("tool_call_id") == "orphan" for m in clean)


def test_sanitizer_keeps_good_messages():
    from core.context import sanitize_messages

    good = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "hello"},
        {"role": "assistant", "content": "hi there"},
    ]
    out = sanitize_messages(good)
    assert [(m["role"], m["content"]) for m in out] == [
        (m["role"], m["content"]) for m in good
    ]
