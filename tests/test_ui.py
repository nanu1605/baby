"""UI server tests: REST + single-session websocket round-trips.

TestClient gives every websocket session its own portal thread/loop, so
cross-socket flows (activity feed + confirm modal) are covered by the
agent-level tests plus the manual checklist — asyncio.Queue is not
thread-safe across portals.
"""

from __future__ import annotations

import asyncio

import pytest
from fastapi.testclient import TestClient

from core.agent import AgentCore
from core.bus import EventBus
from core.safety import SafetyConfig, SafetyGate
from db.database import Database
from tests.conftest import FakeProvider
from ui.server import UIContext, create_app


async def _connect(db: Database) -> int:
    await db.connect()
    return await db.create_conversation("ui")


@pytest.fixture
def ui(tmp_path):
    db = Database(tmp_path / "ui.db")
    conv_id = asyncio.run(_connect(db))
    bus = EventBus()
    gate = SafetyGate(SafetyConfig(mode="dry_run"), bus)
    provider = FakeProvider([])
    agent = AgentCore(provider, db, conv_id, channel="ui", bus=bus, gate=gate)
    ctx = UIContext(
        db=db,
        bus=bus,
        gate=gate,
        agent=agent,
        config={"models": {"daily": {"model": "test-model"}}},
    )
    client = TestClient(create_app(ctx))
    yield client, ctx, provider
    asyncio.run(db.close())


def test_index_serves_html(ui):
    client, _, _ = ui
    resp = client.get("/")
    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]


def test_stats_shape(ui):
    client, _, _ = ui
    data = client.get("/stats").json()
    assert "cpu_percent" in data and "ram" in data
    assert data["model"] == "test-model"
    assert data["turn_running"] is False


def test_confirm_unknown_404(ui):
    client, _, _ = ui
    resp = client.post("/confirm/nope", json={"approved": True})
    assert resp.status_code == 404


def test_kill_without_turn(ui):
    client, _, _ = ui
    assert client.post("/kill").json() == {"cancelled": False}


def test_chat_round_trip_streams(ui):
    client, _, provider = ui
    provider.script = ["hello from baby"]
    with client.websocket_connect("/ws/chat") as ws:
        ws.send_json({"type": "user_message", "text": "hi"})
        kinds = []
        while True:
            msg = ws.receive_json()
            kinds.append(msg["type"])
            if msg["type"] == "turn_end":
                assert msg["reply"] == "hello from baby"
                assert msg["status"] == "ok"
                break
        assert kinds[0] == "turn_start"
        assert "token" in kinds


def test_chat_ignores_blank_and_malformed(ui):
    client, _, provider = ui
    provider.script = ["ok"]
    with client.websocket_connect("/ws/chat") as ws:
        ws.send_json({"type": "user_message", "text": "   "})
        ws.send_json({"type": "something_else"})
        ws.send_json({"type": "user_message", "text": "real"})
        while True:
            if ws.receive_json()["type"] == "turn_end":
                break  # only the real message produced a turn


def test_history_endpoint(ui):
    client, _, provider = ui
    provider.script = ["pong"]
    with client.websocket_connect("/ws/chat") as ws:
        ws.send_json({"type": "user_message", "text": "ping"})
        while ws.receive_json()["type"] != "turn_end":
            pass
    rows = client.get("/history").json()
    assert [r["role"] for r in rows] == ["user", "assistant"]
    assert rows[-1]["content"] == "pong"


# -- Phase 5: project surfaces --------------------------------------------------------


class _StubOrchestrator:
    def __init__(self):
        self._n = 1

    def running_count(self):
        return self._n


def test_projects_endpoint_shape(ui):
    client, ctx, _ = ui

    async def seed():
        pid = await ctx.db.add_project("proj", "spec")
        await ctx.db.add_task("sub", "spec", notify=0, project_id=pid)
        return pid

    pid = asyncio.run(seed())
    rows = client.get("/projects").json()
    assert rows[0]["id"] == pid and rows[0]["title"] == "proj"
    assert rows[0]["subtasks"][0]["title"] == "sub"


def test_stats_projects_running_key(ui):
    client, ctx, _ = ui
    assert "projects_running" not in client.get("/stats").json()
    ctx.orchestrator = _StubOrchestrator()
    assert client.get("/stats").json()["projects_running"] == 1


def test_activity_kinds_include_projects():
    from ui.server import _ACTIVITY_KINDS

    assert {"project_started", "project_done"} <= _ACTIVITY_KINDS
