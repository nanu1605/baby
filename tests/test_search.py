"""B1e: FTS5 search backend — fan-out, quarantine exclusion, injection-safety."""

from __future__ import annotations

import asyncio

from fastapi.testclient import TestClient

from core.agent import AgentCore
from core.bus import EventBus
from core.safety import SafetyConfig, SafetyGate
from db.database import Database, _fts_match
from tests.conftest import FakeProvider
from ui.server import UIContext, create_app


def test_fts_match_is_injection_safe():
    assert _fts_match("") == ""
    assert _fts_match("   ") == ""
    # bare FTS operators / punctuation in the query never reach MATCH as syntax
    m = _fts_match('EV comparison "OR" *')
    assert '"EV"' in m and '"comparison"' in m
    assert m.endswith("*")  # prefix-match the last token


def test_message_search_excludes_quarantined(tmp_path):
    db = Database(tmp_path / "search.db")

    async def run():
        await db.connect()
        conv = await db.create_conversation("ui")
        good = await db.add_message(conv, "user", "compare the Nexon EV and the Tiago", turn_id=1)
        bad = await db.add_message(conv, "user", "compare the poison EV debris row", turn_id=2)
        await db.mark_turn(conv, 2, "failed")  # quarantine the second turn
        hits = await db.search_messages_fts("compare EV")
        ids = {h["id"] for h in hits}
        await db.close()
        return good, bad, ids

    good, bad, ids = asyncio.run(run())
    assert good in ids
    assert bad not in ids  # a quarantined row never surfaces in search


def test_audit_and_task_fts(tmp_path):
    db = Database(tmp_path / "search2.db")

    async def run():
        await db.connect()
        await db.add_audit(
            "ui", "web_search", '{"q":"tata ev"}', "allow", 1, "found 6 results", 20.0
        )
        await db.add_task("Research EV pricing", "compare on-road prices", notify=0)
        audit = await db.search_audit_fts("web_search")
        tasks = await db.search_tasks_fts("EV pricing")
        await db.close()
        return audit, tasks

    audit, tasks = asyncio.run(run())
    assert any(a["tool"] == "web_search" for a in audit)
    assert any("EV" in t["title"] for t in tasks)


def _connect(db: Database) -> int:
    async def go():
        await db.connect()
        return await db.create_conversation("ui")

    return asyncio.run(go())


def test_api_search_groups(tmp_path):
    db = Database(tmp_path / "searchapi.db")
    conv = _connect(db)
    bus = EventBus()
    gate = SafetyGate(SafetyConfig(mode="dry_run"), bus)
    agent = AgentCore(FakeProvider([]), db, conv, channel="ui", bus=bus, gate=gate)
    ctx = UIContext(db=db, bus=bus, gate=gate, agent=agent, config={})
    client = TestClient(create_app(ctx))
    try:
        asyncio.run(
            db.add_audit("ui", "web_search", "{}", "allow", 1, "kanban board results", 10.0)
        )
        asyncio.run(db.add_task("Kanban cleanup", "tidy the board", notify=0))
        data = client.get("/api/search", params={"q": "kanban"}).json()
        assert data["query"] == "kanban"
        assert any(r["node_id"] == "tool:web_search" for r in data["groups"]["activity"])
        assert any(r["node_id"] == "task_queue" for r in data["groups"]["tasks"])
        # empty query → empty groups, never an error
        empty = client.get("/api/search", params={"q": ""}).json()
        assert empty["groups"] == {"facts": [], "conversations": [], "activity": [], "tasks": []}
    finally:
        asyncio.run(db.close())
