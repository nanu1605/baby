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

import pytest

from core.agent import AgentCore
from core.providers.base import Chunk
from db.database import Database


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


def test_sanitizer_preserves_valid_multitool_turn():
    # The normal in-loop turn the agent builds — one assistant with two parallel
    # tool_calls, each answered — must survive untouched and be idempotent.
    from core.context import sanitize_messages

    seq = [
        {"role": "system", "content": "s"},
        {"role": "user", "content": "u"},
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {"id": "a", "type": "function", "function": {"name": "t", "arguments": "{}"}},
                {"id": "b", "type": "function", "function": {"name": "t", "arguments": "{}"}},
            ],
        },
        {"role": "tool", "tool_call_id": "a", "content": "ra"},
        {"role": "tool", "tool_call_id": "b", "content": "rb"},
        {"role": "assistant", "content": "done"},
    ]
    out = sanitize_messages(seq)
    assert out == seq
    assert sanitize_messages(out) == out  # idempotent


# -- layer 1+2: transactional turns + quarantine on failure --------------------


def _tc_chunk(name, args, call_id):
    from core.providers.base import ToolCall

    return [ToolCall(id=call_id, name=name, arguments=json.dumps(args))]


async def test_successful_turn_rows_share_turn_id_and_load(db):
    from tests.conftest import FakeProvider

    conv = await db.create_conversation("cli")
    agent = AgentCore(FakeProvider(["hello"]), db, conv, channel="cli")
    await agent.run_turn("hi")
    rows = await db._fetchall(
        "SELECT role, turn_id, status FROM messages WHERE conversation_id = ?", (conv,)
    )
    assert all(r["status"] == "ok" for r in rows)
    assert len({r["turn_id"] for r in rows}) == 1  # user + assistant, one turn
    loaded = await db.get_messages(conv, roles=("user", "assistant"))
    assert [m["content"] for m in loaded] == ["hi", "hello"]


class _RaisingProvider:
    name = "raiser"

    def __init__(self, exc):
        self.exc = exc

    async def chat(self, messages, tools=None, **opts):
        raise self.exc
        yield  # unreachable — makes this an async generator

    async def healthy(self):
        return True


async def test_errored_turn_is_quarantined_and_excluded(db):
    conv = await db.create_conversation("cli")
    agent = AgentCore(_RaisingProvider(ValueError("boom")), db, conv, channel="cli")
    with pytest.raises(ValueError):
        await agent.run_turn("this will fail")
    # The user row exists but is marked failed, so it never loads to context.
    loaded = await db.get_messages(conv, roles=("user", "assistant"))
    assert loaded == []
    failed = await db.list_messages_by_status(conv, "failed")
    assert any(m["role"] == "user" and m["content"] == "this will fail" for m in failed)


async def test_cancelled_turn_keeps_its_closure_marker(db):
    import asyncio

    class _CancelProvider:
        name = "cancel"

        async def chat(self, messages, tools=None, **opts):
            raise asyncio.CancelledError()
            yield

        async def healthy(self):
            return True

    conv = await db.create_conversation("cli")
    agent = AgentCore(_CancelProvider(), db, conv, channel="cli")
    with pytest.raises(asyncio.CancelledError):
        await agent.run_turn("stop me")
    # A cancelled turn is NOT quarantined — its closure marker must stay in
    # context so later turns do not re-answer the abandoned request.
    loaded = await db.get_messages(conv, roles=("user", "assistant"))
    assert any("stopped by the user" in m["content"] for m in loaded)


# -- reconcile: hard-kill left a turn without an assistant row -----------------


async def test_reconcile_marks_incomplete_turn_failed(tmp_path):
    path = tmp_path / "reconcile.db"
    db1 = Database(path)
    await db1.connect()
    conv = await db1.create_conversation("cli")
    # A complete prior turn...
    await db1.add_message(conv, "user", "q", turn_id=1)
    await db1.add_message(conv, "assistant", "a", turn_id=1)
    # ...and a hard-killed turn: a user row with no assistant.
    await db1.add_message(conv, "user", "unfinished", turn_id=2)
    await db1.close()

    db2 = Database(path)  # reconnect -> _reconcile_incomplete_turns runs
    await db2.connect()
    loaded = [m["content"] for m in await db2.get_messages(conv, roles=("user", "assistant"))]
    assert "q" in loaded and "a" in loaded
    assert "unfinished" not in loaded
    failed = await db2.list_messages_by_status(conv, "failed")
    assert any(m["content"] == "unfinished" for m in failed)
    await db2.close()


# -- layer 4: self-heal when the provider rejects the replayed context ---------


class _HealProvider:
    name = "heal"

    def __init__(self):
        self.calls = 0

    async def chat(self, messages, tools=None, **opts):
        self.calls += 1
        if self.calls == 1:
            raise RuntimeError(
                "400 invalid_request: messages with role 'tool' must be a "
                "response to a preceding message with tool_calls"
            )
            yield
        yield Chunk(delta="healed answer")
        yield Chunk(done=True)


async def test_self_heal_rebuilds_and_retries_once(db):
    conv = await db.create_conversation("cli")
    provider = _HealProvider()
    agent = AgentCore(provider, db, conv, channel="cli")
    reply = await agent.run_turn("hello?")
    assert reply == "healed answer"
    assert provider.calls == 2  # rejected once, healed rebuild succeeded
    # The turn completed OK, so its rows load normally.
    loaded = await db.get_messages(conv, roles=("user", "assistant"))
    assert [m["content"] for m in loaded] == ["hello?", "healed answer"]


class _ToolThenContextError:
    """Tool call on the first generation, then a context error on the next."""

    name = "tce"

    def __init__(self):
        self.calls = 0

    async def chat(self, messages, tools=None, **opts):
        from core.providers.base import ToolCall

        self.calls += 1
        if self.calls == 1:
            yield Chunk(
                tool_calls=[ToolCall(id="c1", name="get_time", arguments="{}")], done=True
            )
        else:
            raise RuntimeError(
                "400 invalid_request: messages with role 'tool' must be a response"
            )
            yield


async def test_self_heal_does_not_fire_after_tool_calls(db):
    # A context rejection AFTER a tool has run must NOT self-heal (that would
    # discard the tool's result). The turn re-raises and is quarantined.
    conv = await db.create_conversation("cli")
    provider = _ToolThenContextError()
    agent = AgentCore(provider, db, conv, channel="cli")
    with pytest.raises(RuntimeError):
        await agent.run_turn("what time is it?")
    assert provider.calls == 2  # iter0 tool call, iter1 raised — no third rebuild
    loaded = await db.get_messages(conv, roles=("user", "assistant"))
    assert loaded == []  # the errored turn is quarantined out of context


# -- reconcile must be conversation-scoped (turn_ids restart per conversation) --


async def test_reconcile_is_conversation_scoped(tmp_path):
    path = tmp_path / "scoped.db"
    db1 = Database(path)
    await db1.connect()
    conv_a = await db1.create_conversation("cli")
    conv_b = await db1.create_conversation("ui")
    # Both conversations independently use turn_id=1. conv_b completed; conv_a
    # was hard-killed (user row, no assistant).
    await db1.add_message(conv_b, "user", "B-q", turn_id=1)
    await db1.add_message(conv_b, "assistant", "B-a", turn_id=1)
    await db1.add_message(conv_a, "user", "A-unfinished", turn_id=1)
    await db1.close()

    db2 = Database(path)  # reconnect -> reconcile runs
    await db2.connect()
    a_loaded = [m["content"] for m in await db2.get_messages(conv_a, roles=("user", "assistant"))]
    b_loaded = [m["content"] for m in await db2.get_messages(conv_b, roles=("user", "assistant"))]
    # conv_a's dangling turn is failed despite conv_b sharing turn_id=1.
    assert "A-unfinished" not in a_loaded
    a_failed = await db2.list_messages_by_status(conv_a, "failed")
    assert any(m["content"] == "A-unfinished" for m in a_failed)
    # conv_b's completed turn is untouched.
    assert b_loaded == ["B-q", "B-a"]
    await db2.close()
