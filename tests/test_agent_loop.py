"""Agent loop contract: tool threading, gate integration, audit, events."""

from __future__ import annotations

import asyncio
import json

from core.agent import MAX_TOOL_ITERATIONS, AgentCore
from core.bus import EventBus
from core.providers.base import ToolCall
from core.safety import SafetyConfig, SafetyGate
from tests.conftest import FakeProvider
from tools import registry
from tools.registry import tool


@tool
def echo_tool(text: str) -> str:
    """Echo the input back."""
    return f"echo:{text}"


async def _make_agent(db, script, *, bus=None, gate=None):
    provider = FakeProvider(script)
    conv_id = await db.create_conversation("cli")
    agent = AgentCore(provider, db, conv_id, channel="cli", bus=bus, gate=gate)
    return agent, provider, conv_id


def _tc(name: str, args: dict, call_id: str = "c1") -> list[ToolCall]:
    return [ToolCall(id=call_id, name=name, arguments=json.dumps(args))]


async def test_plain_text_turn(db):
    agent, _, conv_id = await _make_agent(db, ["hello there"])
    reply = await agent.run_turn("hi")
    assert reply == "hello there"
    rows = await db.get_messages(conv_id)
    assert [r["role"] for r in rows] == ["user", "assistant"]


async def test_tool_call_threads_result_back(db):
    script = [_tc("echo_tool", {"text": "ping"}), "final answer"]
    agent, provider, conv_id = await _make_agent(db, script)
    reply = await agent.run_turn("use the tool")
    assert reply == "final answer"

    second = provider.requests[1]
    tool_msg = next(m for m in second if m["role"] == "tool")
    assert tool_msg["tool_call_id"] == "c1"
    assert tool_msg["content"] == "echo:ping"

    rows = await db.get_messages(conv_id)
    assert [r["role"] for r in rows] == ["user", "tool", "assistant"]


async def test_iteration_cap(db):
    call = _tc("echo_tool", {"text": "loop"}, "cx")
    agent, provider, _ = await _make_agent(db, [call] * (MAX_TOOL_ITERATIONS + 5))
    reply = await agent.run_turn("loop forever")
    assert "limit" in reply.lower()
    assert len(provider.requests) == MAX_TOOL_ITERATIONS


async def test_unknown_tool_returns_error_not_crash(db):
    script = [_tc("no_such_tool", {}), "recovered"]
    agent, provider, _ = await _make_agent(db, script)
    reply = await agent.run_turn("try it")
    assert reply == "recovered"
    tool_msg = next(m for m in provider.requests[1] if m["role"] == "tool")
    assert "unknown tool" in tool_msg["content"]


async def test_history_reloaded_without_tool_messages(db):
    script = [_tc("echo_tool", {"text": "x"}), "done", "second reply"]
    agent, provider, _ = await _make_agent(db, script)
    await agent.run_turn("first")
    await agent.run_turn("second")
    third = provider.requests[2]
    assert all(m["role"] in ("system", "user", "assistant") for m in third)


def test_registry_schema_shape():
    entry = next(s for s in registry.schemas() if s["function"]["name"] == "echo_tool")
    assert entry["type"] == "function"
    assert entry["function"]["parameters"]["required"] == ["text"]


# --- gate + audit + events ------------------------------------------------------


def _gate(bus, mode="enforce"):
    return SafetyGate(SafetyConfig(mode=mode), bus)


async def test_audit_row_written_per_tool_call(db):
    bus = EventBus()
    agent, _, _ = await _make_agent(
        db, [_tc("echo_tool", {"text": "x"}), "done"], bus=bus, gate=_gate(bus)
    )
    await agent.run_turn("go")
    cur = await db.conn.execute("SELECT tool, safety_class, approved FROM audit_log")
    rows = await cur.fetchall()
    assert len(rows) == 1
    assert rows[0]["tool"] == "echo_tool"
    assert rows[0]["safety_class"] == "allow"
    assert rows[0]["approved"] == 1


async def test_deny_threads_error_and_model_recovers(db):
    bus = EventBus()
    script = [_tc("run_shell", {"command": "Stop-Process -Name lsass"}), "refused, boss"]
    agent, provider, _ = await _make_agent(db, script, bus=bus, gate=_gate(bus))
    reply = await agent.run_turn("kill lsass")
    assert reply == "refused, boss"
    tool_msg = next(m for m in provider.requests[1] if m["role"] == "tool")
    assert "denied by safety gate" in tool_msg["content"]
    cur = await db.conn.execute("SELECT approved, safety_class FROM audit_log")
    row = await cur.fetchone()
    assert row["approved"] == 0
    assert row["safety_class"] == "deny"


async def test_confirm_approved_runs_dry_run(db):
    bus = EventBus()
    gate = _gate(bus, mode="dry_run")
    q = bus.subscribe()
    script = [_tc("run_shell", {"command": "mkdir foo"}), "made it"]
    agent, provider, _ = await _make_agent(db, script, bus=bus, gate=gate)

    async def approver():
        while True:
            event = await q.get()
            if event.kind == "confirm_request":
                gate.confirmations.resolve(event.payload["confirm_id"], True)
                return

    approver_task = asyncio.create_task(approver())
    reply = await agent.run_turn("make a folder")
    await approver_task
    assert reply == "made it"
    tool_msg = next(m for m in provider.requests[1] if m["role"] == "tool")
    assert json.loads(tool_msg["content"])["dry_run"] is True
    cur = await db.conn.execute("SELECT approved FROM audit_log")
    assert (await cur.fetchone())["approved"] == 1


async def test_confirm_timeout_feeds_error_back(db):
    bus = EventBus()
    gate = SafetyGate(SafetyConfig(mode="dry_run", confirm_timeout_s=0.05), bus)
    script = [_tc("run_shell", {"command": "mkdir foo"}), "ok, skipped it"]
    agent, provider, _ = await _make_agent(db, script, bus=bus, gate=gate)
    reply = await agent.run_turn("make a folder")
    assert reply == "ok, skipped it"
    tool_msg = next(m for m in provider.requests[1] if m["role"] == "tool")
    assert "timeout" in tool_msg["content"]
    cur = await db.conn.execute("SELECT approved FROM audit_log")
    assert (await cur.fetchone())["approved"] == 0


async def test_turn_events_published(db):
    bus = EventBus()
    q = bus.subscribe()
    agent, _, _ = await _make_agent(
        db, [_tc("echo_tool", {"text": "x"}), "done"], bus=bus, gate=_gate(bus)
    )
    await agent.run_turn("go")
    kinds = []
    while not q.empty():
        kinds.append(q.get_nowait().kind)
    assert kinds[0] == "turn_start"
    assert kinds[-1] == "turn_end"
    assert "tool_start" in kinds and "tool_end" in kinds and "token" in kinds


async def test_cancellation_publishes_cancelled_turn_end(db):
    bus = EventBus()
    q = bus.subscribe()

    class HangingProvider(FakeProvider):
        async def chat(self, messages, tools=None, **opts):
            self.requests.append(messages)
            await asyncio.sleep(30)
            yield None  # pragma: no cover

    provider = HangingProvider([])
    conv_id = await db.create_conversation("cli")
    agent = AgentCore(provider, db, conv_id, channel="cli", bus=bus, gate=_gate(bus))
    task = asyncio.create_task(agent.run_turn("hang"))
    await asyncio.sleep(0.05)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    events = []
    while not q.empty():
        events.append(q.get_nowait())
    end = next(e for e in events if e.kind == "turn_end")
    assert end.payload["status"] == "cancelled"


# -- empty final reply (thinking model burned the window) ---------------------------


async def test_empty_final_reply_retries_with_thinking_off(db):
    script = [_tc("echo_tool", {"text": "x"}), "", "Recovered answer."]
    agent, provider, _ = await _make_agent(db, script)
    reply = await agent.run_turn("do it")
    assert reply == "Recovered answer."
    assert provider.request_tools[-1] is None  # finalize call carries no tools
    assert provider.requests[-1][-1]["role"] == "system"  # answer-now nudge appended


async def test_empty_reply_without_tools_stays_placeholder(db):
    agent, provider, _ = await _make_agent(db, [""])
    reply = await agent.run_turn("hi")
    assert reply == "(no response)"
    assert len(provider.requests) == 1  # no retry when no tool ran
