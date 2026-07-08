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


# -- B0: dual-UI flag (v3 shell vs classic rollback) --------------------------

_CLASSIC_MARKER = "/static/app.js"  # only the vanilla ui/web shell references this


def _v3_client(tmp_path, monkeypatch, dist_dir, frontend):
    """A TestClient whose APP_DIST points at `dist_dir` (real npm build not
    required) with the given ui.frontend flag."""
    from ui import server as srv

    monkeypatch.setattr(srv, "APP_DIST", dist_dir)
    db = Database(tmp_path / "dual.db")
    conv_id = asyncio.run(_connect(db))
    bus = EventBus()
    gate = SafetyGate(SafetyConfig(mode="dry_run"), bus)
    agent = AgentCore(FakeProvider([]), db, conv_id, channel="ui", bus=bus, gate=gate)
    ctx = UIContext(db=db, bus=bus, gate=gate, agent=agent, config={"ui": {"frontend": frontend}})
    return TestClient(srv.create_app(ctx)), db


def test_default_frontend_is_classic(ui):
    client, _, _ = ui  # fixture config has no ui.frontend key
    assert _CLASSIC_MARKER in client.get("/").text


def test_classic_route_always_served(ui):
    client, _, _ = ui
    resp = client.get("/classic")
    assert resp.status_code == 200
    assert _CLASSIC_MARKER in resp.text


def test_v3_frontend_served_when_built(tmp_path, monkeypatch):
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<!doctype html><div id=root>V3-SPA</div>", encoding="utf-8")
    client, db = _v3_client(tmp_path, monkeypatch, dist, "v3")
    try:
        assert "V3-SPA" in client.get("/").text  # SPA served at /
        assert _CLASSIC_MARKER in client.get("/classic").text  # rollback still works
    finally:
        asyncio.run(db.close())


def test_v3_flag_falls_back_to_classic_when_unbuilt(tmp_path, monkeypatch):
    client, db = _v3_client(tmp_path, monkeypatch, tmp_path / "no-dist", "v3")
    try:
        assert _CLASSIC_MARKER in client.get("/").text  # graceful fallback, no crash
    finally:
        asyncio.run(db.close())


def test_stats_shape(ui):
    client, _, _ = ui
    data = client.get("/stats").json()
    assert "cpu_percent" in data and "ram" in data
    assert data["model"] == "test-model"
    assert data["turn_running"] is False


def test_stats_tokens_block(ui):
    client, ctx, _ = ui
    # Empty usage_log: the block is present and zeroed, never missing.
    data = client.get("/stats").json()
    assert data["tokens"]["today"]["total"] == 0
    assert data["tokens"]["session"]["total"] == 0
    # A recorded turn shows up in today + session, grouped by brain.
    ctx.session_start = "2000-01-01 00:00:00"  # capture all rows deterministically
    asyncio.run(ctx.db.add_usage(
        ctx.agent.conversation_id, 1, "ui", "nim_primary", "m/x",
        {"prompt": 12, "completion": 8, "total": 20},
    ))
    data = client.get("/stats").json()
    assert data["tokens"]["today"]["total"] == 20
    assert data["tokens"]["today"]["by_brain"] == {"nim_primary": 20}
    assert data["tokens"]["session"]["total"] == 20


def test_confirm_unknown_404(ui):
    client, _, _ = ui
    resp = client.post("/confirm/nope", json={"approved": True})
    assert resp.status_code == 404


def test_kill_without_turn(ui):
    client, _, _ = ui
    assert client.post("/kill").json() == {"cancelled": False}


def test_conversation_new_rotates_and_clears_history(ui):
    client, ctx, provider = ui
    provider.script = ["first reply"]
    with client.websocket_connect("/ws/chat") as ws:
        ws.send_json({"type": "user_message", "text": "hello"})
        while ws.receive_json()["type"] != "turn_end":
            pass
    assert len(client.get("/history").json()) == 2  # user + assistant
    old_id = ctx.agent.conversation_id
    resp = client.post("/conversation/new")
    assert resp.status_code == 200
    assert ctx.agent.conversation_id != old_id
    assert client.get("/history").json() == []  # fresh context


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


def test_ws_game_command_bypasses_model(ui):
    """Escape hatch: bare game-mode text toggles with ZERO model calls."""
    client, ctx, provider = ui
    calls = []

    async def fake_set(on):
        calls.append(on)
        return "game mode ON - local brain unloaded, cloud answers now"

    provider.set_game_mode = fake_set
    provider.game_mode = True
    with client.websocket_connect("/ws/chat") as ws:
        ws.send_json({"type": "user_message", "text": "game mode on"})
        msgs = [ws.receive_json() for _ in range(3)]
    assert calls == [True]
    assert [m["type"] for m in msgs] == ["turn_start", "token", "turn_end"]
    assert provider.requests == []  # the model never ran


# -- P4e: memory browser endpoints --------------------------------------------


def _ui_with_memory(tmp_path):
    from memory import Memory
    from memory.extractor import FactExtractor
    from memory.store import MemoryStore
    from memory.summarizer import Summarizer
    from tests.test_memory import FakeEmbedder

    db = Database(tmp_path / "uimem.db")
    conv_id = asyncio.run(_connect(db))
    bus = EventBus()
    gate = SafetyGate(SafetyConfig(mode="dry_run"), bus)
    provider = FakeProvider([])

    async def build():
        store = MemoryStore(db, FakeEmbedder(), k=5, min_similarity=0.1, dedup_similarity=0.9)
        await store.init()
        return store

    store = asyncio.run(build())
    mem = Memory(
        store=store,
        summarizer=Summarizer(provider, db),
        extractor=FactExtractor(provider, db, store),
    )
    agent = AgentCore(provider, db, conv_id, channel="ui", bus=bus, gate=gate, memory=mem)
    ctx = UIContext(db=db, bus=bus, gate=gate, agent=agent, config={}, memory=mem)
    return TestClient(create_app(ctx)), ctx, store, db


def test_memory_delete_fact(tmp_path):
    client, _, store, db = _ui_with_memory(tmp_path)
    try:
        fid = asyncio.run(store.add_fact("owner likes chai"))["id"]
        assert any(f["id"] == fid for f in client.get("/memory").json())
        resp = client.request("DELETE", f"/memory/fact/{fid}")
        assert resp.status_code == 200 and resp.json()["deleted"] == fid
        assert not any(f["id"] == fid for f in client.get("/memory").json())
    finally:
        asyncio.run(db.close())


def test_memory_wipe_requires_typed_phrase(tmp_path):
    client, ctx, store, db = _ui_with_memory(tmp_path)
    try:
        asyncio.run(store.add_fact("secret"))
        old_conv = ctx.agent.conversation_id
        assert client.post("/memory/wipe", json={"phrase": "nope"}).status_code == 400
        assert asyncio.run(store.count_active()) == 1  # nothing erased
        resp = client.post("/memory/wipe", json={"phrase": "WIPE"})
        assert resp.status_code == 200
        assert asyncio.run(store.count_active()) == 0  # wiped
        assert ctx.agent.conversation_id != old_conv  # live session flushed
    finally:
        asyncio.run(db.close())


def test_memory_endpoints_404_without_memory(ui):
    client, _, _ = ui  # the default fixture has no memory
    assert client.request("DELETE", "/memory/fact/1").status_code == 404
    assert client.post("/memory/wipe", json={"phrase": "WIPE"}).status_code == 404
