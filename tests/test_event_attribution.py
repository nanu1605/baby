"""B1b: additive source/target/turn_id on bus events.

Every event gains node-ids so the v3 graph can pulse a turn along its real path.
The fields ride in the freeform payload (bus.publish(**payload)) — zero signature
change — and are additive, so /classic (which ignores unknown keys) is unaffected.
"""

from __future__ import annotations

import json

import pytest

from core.agent import AgentCore
from core.bus import EventBus
from core.providers.base import ToolCall
from core.safety import SafetyConfig, SafetyGate
from tests.conftest import FakeProvider
from tools.registry import tool

pytestmark = pytest.mark.asyncio


@tool
def _attr_echo(text: str) -> str:
    """Echo tool for attribution tests."""
    return f"echo:{text}"


class _ActiveProvider(FakeProvider):
    """FakeProvider that reports a routing decision, like the live CloudRouter."""

    def __init__(self, script):
        super().__init__(script)
        self.active = {"tier": "nim_primary", "model": "test/model"}


def _drain(q):
    out = []
    while not q.empty():
        out.append(q.get_nowait())
    return out


async def test_turn_events_carry_source_and_turn_id(db):
    conv = await db.create_conversation("ui")
    bus = EventBus()
    q = bus.subscribe()
    gate = SafetyGate(SafetyConfig(mode="dry_run"), bus)
    agent = AgentCore(_ActiveProvider(["hi there"]), db, conv, channel="ui", bus=bus, gate=gate)
    await agent.run_turn("hello")
    events = _drain(q)

    start = next(e for e in events if e.kind == "turn_start")
    assert start.payload["source"] == "baby_core"
    assert start.payload["turn_id"] is not None

    tok = next(e for e in events if e.kind == "token")
    assert tok.payload["source"] == "brain:nim_primary"

    end = next(e for e in events if e.kind == "turn_end")
    assert end.payload["source"] == "brain:nim_primary"
    assert end.payload["turn_id"] == start.payload["turn_id"]


async def test_tool_events_carry_source_target_turn_id(db):
    conv = await db.create_conversation("ui")
    bus = EventBus()
    q = bus.subscribe()
    gate = SafetyGate(SafetyConfig(mode="dry_run"), bus)
    # round 1 → a tool call; round 2 → the final text answer
    script = [[ToolCall(id="c1", name="_attr_echo", arguments=json.dumps({"text": "x"}))], "done"]
    agent = AgentCore(_ActiveProvider(script), db, conv, channel="ui", bus=bus, gate=gate)
    await agent.run_turn("use the tool")
    events = _drain(q)

    start = next(e for e in events if e.kind == "tool_start")
    end = next(e for e in events if e.kind == "tool_end")
    for e in (start, end):
        assert e.payload["source"] == "brain:nim_primary"
        assert e.payload["target"] == "tool:_attr_echo"
        assert e.payload["turn_id"] is not None
    # tool events share the turn's id
    assert start.payload["turn_id"] == end.payload["turn_id"]


async def test_source_defaults_to_local_brain_without_router(db):
    # A plain provider (no .active) still attributes tokens to a real node id.
    conv = await db.create_conversation("ui")
    bus = EventBus()
    q = bus.subscribe()
    gate = SafetyGate(SafetyConfig(mode="dry_run"), bus)
    agent = AgentCore(FakeProvider(["hey"]), db, conv, channel="ui", bus=bus, gate=gate)
    await agent.run_turn("hi")
    tok = next(e for e in _drain(q) if e.kind == "token")
    assert tok.payload["source"] == "brain:daily"
