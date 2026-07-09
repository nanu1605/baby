"""B4 controls: tool_flags gating, brain boost, task-cancel / scheduler-run
endpoints, and the safety-gate-immutability contract at the flag seam.

The gate is exercised in tests/test_safety.py; here we prove the disable seam is
disjoint from it and that the control endpoints behave.
"""

from __future__ import annotations

import asyncio

from fastapi.testclient import TestClient

from core.agent import AgentCore
from core.bus import EventBus
from core.safety import SafetyConfig, SafetyGate
from db.database import Database
from tests.conftest import FakeProvider
from tools import registry
from tools.registry import tool
from ui.server import UIContext, create_app

_CONFIG = {
    "models": {
        "daily": {"provider": "ollama", "model": "qwen3.5:9b-q4_K_M"},
        "nim_heavy": {"provider": "nvidia", "model": "z-ai/glm-5.2"},
        "cloud": {"provider": "gemini", "model": "gemini-flash-latest"},
    },
    "router": {"primary": "nim_primary", "heavy": "nim_heavy", "backstop": "cloud"},
}


@tool
def _b4_flag_probe(x: str) -> str:
    """A dummy tool for B4 tool_flags tests."""
    return x


def _client(tmp_path):
    db = Database(tmp_path / "b4.db")
    conv = asyncio.run(_connect(db))
    bus = EventBus()
    gate = SafetyGate(SafetyConfig(mode="dry_run"), bus)
    agent = AgentCore(FakeProvider([]), db, conv, channel="ui", bus=bus, gate=gate)
    ctx = UIContext(db=db, bus=bus, gate=gate, agent=agent, config=_CONFIG)
    return db, agent, TestClient(create_app(ctx))


async def _connect(db: Database) -> int:
    await db.connect()
    return await db.create_conversation("ui")


# -- DB + registry layer -------------------------------------------------------


def test_disabled_tools_roundtrip(tmp_path):
    db = Database(tmp_path / "flags.db")

    async def go():
        await db.connect()
        assert await db.disabled_tools() == set()
        await db.set_tool_flag("_b4_flag_probe", False)
        assert "_b4_flag_probe" in await db.disabled_tools()
        await db.set_tool_flag("_b4_flag_probe", True)  # re-enable
        assert await db.disabled_tools() == set()
        await db.close()

    asyncio.run(go())


def test_schemas_filter_hides_disabled():
    names = {s["function"]["name"] for s in registry.schemas()}
    assert "_b4_flag_probe" in names
    filtered = {s["function"]["name"] for s in registry.schemas({"_b4_flag_probe"})}
    assert "_b4_flag_probe" not in filtered
    assert registry.is_registered("_b4_flag_probe") is True
    assert registry.is_registered("safety_gate") is False  # the gate is not a tool


# -- endpoints -----------------------------------------------------------------


def test_tool_flag_endpoint_and_stats(tmp_path):
    db, _agent, client = _client(tmp_path)
    try:
        # disable → stats reflects it + disabled_tools carries it
        r = client.post("/api/tools/_b4_flag_probe/flag", json={"enabled": False})
        assert r.status_code == 200 and r.json()["enabled"] is False
        stats = client.get("/api/nodes/tool:_b4_flag_probe/stats").json()
        assert stats["enabled"] is False
        # unknown/non-tool name (e.g. the gate) is rejected
        bad = client.post("/api/tools/safety_gate/flag", json={"enabled": False})
        assert bad.status_code == 404
    finally:
        asyncio.run(db.close())


def test_brain_boost_arms_one_shot(tmp_path):
    db, agent, client = _client(tmp_path)
    try:
        assert agent._tier_hint_once is None
        assert client.post("/api/brain/boost", json={"on": True}).json()["armed"] is True
        assert agent._tier_hint_once == "best"
        assert client.get("/api/nodes/brain:nim_heavy/stats").json()["pinned_next_turn"] is True
        # disarm
        client.post("/api/brain/boost", json={"on": False})
        assert agent._tier_hint_once is None
    finally:
        asyncio.run(db.close())


def test_cancel_and_run_endpoints_guard_missing_subsystems(tmp_path):
    db, _agent, client = _client(tmp_path)
    try:
        # no pool / scheduler attached in this minimal ctx → 404, never a 500
        assert client.post("/api/tasks/1/cancel").status_code == 404
        assert client.post("/api/scheduler/morning-briefing/run").status_code == 404
    finally:
        asyncio.run(db.close())
