"""P5 token telemetry: per-turn aggregation + usage_log persistence.

The provider seam (Chunk.usage) is covered in test_provider_usage; here the
agent sums a turn's generations into one usage_log row and the DB aggregates
by day / session / brain.
"""

from __future__ import annotations

import json

from core.agent import AgentCore
from core.bus import EventBus
from core.providers.base import Chunk, ToolCall
from tests.conftest import FakeProvider
from tools.registry import tool

pytestmark = __import__("pytest").mark.asyncio


@tool
def usage_echo(text: str) -> str:
    """Echo helper for usage tests."""
    return f"echo:{text}"


def _u(prompt, completion):
    """Chunk.usage shape (what a provider emits)."""
    return {
        "prompt_tokens": prompt,
        "completion_tokens": completion,
        "total_tokens": prompt + completion,
    }


def _tok(prompt, completion):
    """add_usage() tokens shape (the agent's aggregated _turn_tokens)."""
    return {"prompt": prompt, "completion": completion, "total": prompt + completion}


class UsageProvider(FakeProvider):
    """FakeProvider that attaches a scripted usage dict to each done chunk."""

    def __init__(self, script, usages, active=None):
        super().__init__(script)
        self.usages = list(usages)
        self.active = active or {"tier": "nim_primary", "model": "test/model"}

    async def chat(self, messages, tools=None, **opts):
        self.requests.append([dict(m) for m in messages])
        self.request_tools.append(tools)
        step = self.script.pop(0) if self.script else "(exhausted)"
        usage = self.usages.pop(0) if self.usages else None
        if isinstance(step, str):
            yield Chunk(delta=step)
            yield Chunk(done=True, usage=usage)
        else:
            yield Chunk(tool_calls=step, done=True, usage=usage)


def _tc(name, args, call_id="c1"):
    return [ToolCall(id=call_id, name=name, arguments=json.dumps(args))]


# -- agent aggregation --------------------------------------------------------


async def test_accrue_tokens_sums_and_ignores_empty(db):
    conv = await db.create_conversation("cli")
    agent = AgentCore(FakeProvider([]), db, conv, channel="cli")
    agent._turn_tokens = {"prompt": 0, "completion": 0, "total": 0}
    agent._accrue_tokens(_u(10, 4))
    agent._accrue_tokens(None)  # no usage → no-op
    agent._accrue_tokens(_u(3, 2))
    assert agent._turn_tokens == {"prompt": 13, "completion": 6, "total": 19}


async def test_plain_turn_writes_one_usage_row(db):
    provider = UsageProvider(["hello"], [_u(20, 5)])
    conv = await db.create_conversation("cli")
    agent = AgentCore(provider, db, conv, channel="cli")
    await agent.run_turn("hi")

    rows = await db._fetchall("SELECT * FROM usage_log")
    assert len(rows) == 1
    row = rows[0]
    assert (row["prompt_tokens"], row["completion_tokens"], row["total_tokens"]) == (20, 5, 25)
    assert row["turn_id"] is not None
    assert row["brain_tier"] == "nim_primary" and row["brain_model"] == "test/model"


async def test_multi_round_turn_sums_into_single_row(db):
    # A tool round (gen 1) + the final answer (gen 2) → ONE row, summed.
    provider = UsageProvider(
        [_tc("usage_echo", {"text": "x"}), "done"], [_u(30, 0), _u(10, 20)]
    )
    conv = await db.create_conversation("cli")
    agent = AgentCore(provider, db, conv, channel="cli")
    await agent.run_turn("use the tool")

    rows = await db._fetchall("SELECT * FROM usage_log")
    assert len(rows) == 1
    assert (rows[0]["prompt_tokens"], rows[0]["completion_tokens"], rows[0]["total_tokens"]) == (
        40, 20, 60,
    )


async def test_turn_end_payload_carries_tokens(db):
    bus = EventBus()
    q = bus.subscribe()
    provider = UsageProvider(["hi"], [_u(7, 3)])
    conv = await db.create_conversation("cli")
    agent = AgentCore(provider, db, conv, channel="cli", bus=bus)
    await agent.run_turn("hey")
    events = []
    while not q.empty():
        events.append(q.get_nowait())
    end = next(e for e in events if e.kind == "turn_end")
    assert end.payload["tokens"] == {"prompt": 7, "completion": 3, "total": 10}


async def test_zero_usage_turn_writes_no_row(db):
    # A provider that reports no usage (e.g. host without include_usage) must not
    # create an empty row — telemetry stays blank, never a crash.
    provider = FakeProvider(["hello"])  # no usage on its chunks
    conv = await db.create_conversation("cli")
    agent = AgentCore(provider, db, conv, channel="cli")
    await agent.run_turn("hi")
    rows = await db._fetchall("SELECT * FROM usage_log")
    assert rows == []


# -- DB aggregation -----------------------------------------------------------


async def test_usage_today_and_session_group_by_brain(db):
    conv = await db.create_conversation("cli")
    await db.add_usage(conv, 1, "cli", "nim_primary", "m/p", _tok(100, 40))
    await db.add_usage(conv, 2, "ui", "daily", "qwen", _tok(60, 20))
    await db.add_usage(conv, 3, "cli", "nim_primary", "m/p", _tok(10, 5))

    today = await db.usage_today()
    assert today["total"] == 235
    assert today["prompt"] == 170 and today["completion"] == 65
    assert today["by_brain"] == {"nim_primary": 155, "daily": 80}

    session = await db.usage_session("2000-01-01T00:00:00")
    assert session["total"] == 235
    # A future watermark captures nothing.
    empty = await db.usage_session("2999-01-01T00:00:00")
    assert empty["total"] == 0 and empty["by_brain"] == {}
