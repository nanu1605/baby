"""B1 graph data spine: topology derivation + /api/graph endpoint.

Topology is derived, not hand-maintained: brain nodes come from config, tool
nodes from the live registry (add a @tool → a node appears), and the static
call-path edges route every brain through the safety gate.
"""

from __future__ import annotations

import asyncio

from fastapi.testclient import TestClient

from core.agent import AgentCore
from core.bus import AgentEvent, EventBus
from core.nodes import build_graph
from core.safety import SafetyConfig, SafetyGate
from db.database import Database
from tests.conftest import FakeProvider
from tools.registry import tool
from ui.server import UIContext, create_app

_CONFIG = {
    "models": {
        "daily": {"provider": "ollama", "model": "qwen3.5:9b-q4_K_M"},
        "nim_primary": {"provider": "nvidia", "model": "openai/gpt-4o-mini"},
        "nim_heavy": {"provider": "nvidia", "model": "z-ai/glm-5.2"},
        "cloud": {"provider": "gemini", "model": "gemini-flash-latest"},
        "embedder": "intfloat/multilingual-e5-small",
    },
    "router": {
        "primary": "nim_primary",
        "heavy": "nim_heavy",
        "backstop": "cloud",
        "offline_fallback": "daily",
    },
}


async def _connect(db: Database) -> int:
    await db.connect()
    return await db.create_conversation("ui")


def test_build_graph_derives_brains_from_config():
    g = build_graph(_CONFIG, tool_schemas=[])
    ids = {n["id"] for n in g["nodes"]}
    assert {"brain:daily", "brain:nim_primary", "brain:nim_heavy", "brain:cloud"} <= ids
    assert "brain:embedder" not in ids  # embedder is not a brain
    prim = next(n for n in g["nodes"] if n["id"] == "brain:nim_primary")
    assert prim["label"] == "openai/gpt-4o-mini"
    assert "primary" in prim["role"]


def test_build_graph_has_fixed_subsystems():
    g = build_graph(_CONFIG, tool_schemas=[])
    ids = {n["id"] for n in g["nodes"]}
    assert {"baby_core", "router", "safety_gate", "mem_facts", "voice_stt", "task_queue"} <= ids
    gate = next(n for n in g["nodes"] if n["id"] == "safety_gate")
    assert gate["type"] == "safety"  # distinct type — B4 immutability anchor


def test_build_graph_derives_tool_node():
    @tool
    def _b1_dummy_probe(x: str) -> str:
        """A dummy probe tool for B1 topology derivation."""
        return x

    g = build_graph(_CONFIG)  # None → live registry
    node = next((n for n in g["nodes"] if n["id"] == "tool:_b1_dummy_probe"), None)
    assert node is not None
    assert node["type"] == "tool"
    assert node["blurb"].startswith("A dummy probe tool")


def test_edges_route_through_the_gate():
    g = build_graph(
        _CONFIG, tool_schemas=[{"function": {"name": "clock_now", "description": "time"}}]
    )
    pairs = {(e["source"], e["target"]) for e in g["edges"]}
    assert ("router", "brain:nim_primary") in pairs
    assert ("brain:nim_primary", "safety_gate") in pairs
    assert ("safety_gate", "tool:clock_now") in pairs
    assert ("voice_stt", "router") in pairs


def test_api_graph_endpoint(tmp_path):
    db = Database(tmp_path / "graph.db")
    conv = asyncio.run(_connect(db))
    bus = EventBus()
    gate = SafetyGate(SafetyConfig(mode="dry_run"), bus)
    agent = AgentCore(FakeProvider([]), db, conv, channel="ui", bus=bus, gate=gate)
    ctx = UIContext(db=db, bus=bus, gate=gate, agent=agent, config=_CONFIG)
    client = TestClient(create_app(ctx))
    try:
        data = client.get("/api/graph").json()
        assert "nodes" in data and "edges" in data
        ids = {n["id"] for n in data["nodes"]}
        assert "brain:nim_primary" in ids and "safety_gate" in ids
    finally:
        asyncio.run(db.close())


# -- B1c: /ws/state synthesized pipeline states -------------------------------


def _ev(kind, channel="ui", **payload):
    return AgentEvent(kind=kind, channel=channel, payload=payload)


def test_state_deriver_turn_timeline():
    from ui.server import _StateDeriver

    d = _StateDeriver()
    assert d.state == "idle"
    assert d.feed(_ev("turn_start")) == "thinking"
    assert d.feed(_ev("token", text="hi")) == "speaking"
    assert d.feed(_ev("turn_end")) == "idle"


def test_state_deriver_tracks_open_tools():
    from ui.server import _StateDeriver

    d = _StateDeriver()
    d.feed(_ev("turn_start"))
    assert d.feed(_ev("tool_start", call_id="a")) == "executing"
    assert d.feed(_ev("tool_start", call_id="b")) == "executing"
    assert d.feed(_ev("tool_end", call_id="a")) == "executing"  # b still open
    assert d.feed(_ev("tool_end", call_id="b")) == "thinking"  # all closed
    assert d.feed(_ev("turn_end")) == "idle"


def test_state_deriver_voice_and_non_voice_status():
    from ui.server import _StateDeriver

    d = _StateDeriver()
    assert d.feed(_ev("status", channel="voice", text="voice: listening")) == "listening"
    assert d.feed(_ev("status", channel="voice", text="voice: heard nothing")) == "idle"
    d.state = "thinking"
    # a router status must not move the gauge
    assert d.feed(_ev("status", channel="router", text="router: using daily")) == "thinking"


# -- B1d: node stats ----------------------------------------------------------


def test_audit_stats_and_usage_by_brain(tmp_path):
    db = Database(tmp_path / "stats.db")

    async def run():
        await db.connect()
        conv = await db.create_conversation("ui")
        # 3 calls to clock_now: 2 ok (10ms, 30ms), 1 denied (no duration)
        await db.add_audit("ui", "clock_now", "{}", "allow", 1, "ok", 10.0)
        await db.add_audit("ui", "clock_now", "{}", "allow", 1, "ok", 30.0)
        await db.add_audit("ui", "clock_now", "{}", "deny", 0, "denied", None)
        stats = await db.audit_stats("clock_now")
        await db.add_usage(
            conv, 1, "ui", "nim_primary", "m", {"prompt": 100, "completion": 40, "total": 140}
        )
        usage = await db.usage_by_brain("nim_primary")
        await db.close()
        return stats, usage

    stats, usage = asyncio.run(run())
    assert stats["calls_window"] == 3
    assert stats["errors"] == 1
    assert stats["error_rate"] == round(1 / 3, 3)
    assert stats["p50_ms"] == 20.0  # interpolated median of [10, 30]
    assert stats["p95_ms"] is not None
    assert stats["last_ts"] is not None
    assert usage["total"] == 140 and usage["turns"] == 1


def _stats_client(tmp_path):
    db = Database(tmp_path / "nodestats.db")
    conv = asyncio.run(_connect(db))
    bus = EventBus()
    gate = SafetyGate(SafetyConfig(mode="dry_run"), bus)
    agent = AgentCore(FakeProvider([]), db, conv, channel="ui", bus=bus, gate=gate)
    ctx = UIContext(db=db, bus=bus, gate=gate, agent=agent, config=_CONFIG)
    return TestClient(create_app(ctx)), db


def test_api_node_stats_dispatch(tmp_path):
    client, db = _stats_client(tmp_path)
    try:
        asyncio.run(db.add_audit("ui", "clock_now", "{}", "allow", 1, "ok", 12.0))
        tool_stats = client.get("/api/nodes/tool:clock_now/stats").json()
        assert tool_stats["type"] == "tool" and tool_stats["calls_window"] == 1

        brain_stats = client.get("/api/nodes/brain:nim_primary/stats").json()
        assert brain_stats["type"] == "brain" and "latency_ms" in brain_stats
        assert "tokens" in brain_stats

        tq = client.get("/api/nodes/task_queue/stats").json()
        assert tq["type"] == "infra" and "queued" in tq

        sub = client.get("/api/nodes/router/stats").json()
        assert sub["type"] == "subsystem"  # no dedicated stats, still a valid payload
    finally:
        asyncio.run(db.close())
