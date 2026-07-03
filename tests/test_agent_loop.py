"""Agent loop contract: tool threading, iteration cap, persistence."""

from __future__ import annotations

import json

from core.agent import MAX_TOOL_ITERATIONS, AgentCore
from core.providers.base import ToolCall
from tests.conftest import FakeProvider
from tools import registry
from tools.registry import tool


@tool
def echo_tool(text: str) -> str:
    """Echo the input back."""
    return f"echo:{text}"


async def _make_agent(db, script):
    provider = FakeProvider(script)
    conv_id = await db.create_conversation("cli")
    return AgentCore(provider, db, conv_id), provider, conv_id


async def test_plain_text_turn(db):
    agent, _, conv_id = await _make_agent(db, ["hello there"])
    reply = await agent.run_turn("hi")
    assert reply == "hello there"
    rows = await db.get_messages(conv_id)
    assert [r["role"] for r in rows] == ["user", "assistant"]


async def test_tool_call_threads_result_back(db):
    script = [
        [ToolCall(id="c1", name="echo_tool", arguments=json.dumps({"text": "ping"}))],
        "final answer",
    ]
    agent, provider, conv_id = await _make_agent(db, script)
    reply = await agent.run_turn("use the tool")
    assert reply == "final answer"

    # Second model request must contain the assistant tool_calls msg + tool result.
    second = provider.requests[1]
    roles = [m["role"] for m in second]
    assert "tool" in roles
    tool_msg = next(m for m in second if m["role"] == "tool")
    assert tool_msg["tool_call_id"] == "c1"
    assert tool_msg["content"] == "echo:ping"

    # Tool activity persisted.
    rows = await db.get_messages(conv_id)
    assert [r["role"] for r in rows] == ["user", "tool", "assistant"]


async def test_iteration_cap(db):
    call = [ToolCall(id="cx", name="echo_tool", arguments=json.dumps({"text": "loop"}))]
    agent, provider, _ = await _make_agent(db, [call] * (MAX_TOOL_ITERATIONS + 5))
    reply = await agent.run_turn("loop forever")
    assert "limit" in reply.lower()
    assert len(provider.requests) == MAX_TOOL_ITERATIONS


async def test_unknown_tool_returns_error_not_crash(db):
    script = [
        [ToolCall(id="c1", name="no_such_tool", arguments="{}")],
        "recovered",
    ]
    agent, provider, _ = await _make_agent(db, script)
    reply = await agent.run_turn("try it")
    assert reply == "recovered"
    tool_msg = next(m for m in provider.requests[1] if m["role"] == "tool")
    assert "unknown tool" in tool_msg["content"]


async def test_history_reloaded_without_tool_messages(db):
    script = [
        [ToolCall(id="c1", name="echo_tool", arguments=json.dumps({"text": "x"}))],
        "done",
        "second reply",
    ]
    agent, provider, _ = await _make_agent(db, script)
    await agent.run_turn("first")
    await agent.run_turn("second")
    # Third request = start of turn 2: history must hold no raw 'tool' rows.
    third = provider.requests[2]
    assert all(m["role"] in ("system", "user", "assistant") for m in third)


def test_registry_schema_shape():
    entry = next(s for s in registry.schemas() if s["function"]["name"] == "echo_tool")
    assert entry["type"] == "function"
    assert entry["function"]["parameters"]["required"] == ["text"]
    assert entry["function"]["parameters"]["properties"]["text"]["type"] == "string"
