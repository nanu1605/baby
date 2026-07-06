"""Phase 4 stage 1: task/schedule CRUD + per-instance agent iteration budget."""

from __future__ import annotations

import pytest

from core.agent import AgentCore
from core.providers.base import ToolCall
from tests.conftest import FakeProvider

pytestmark = pytest.mark.asyncio


# -- tasks ------------------------------------------------------------------


async def test_add_and_get_task(db):
    task_id = await db.add_task("research EVs", "find top 3 EVs under 15 lakh")
    task = await db.get_task(task_id)
    assert task["title"] == "research EVs"
    assert task["status"] == "queued"
    assert task["notify"] == 1
    assert task["started_at"] is None and task["finished_at"] is None


async def test_claim_empty_queue_returns_none(db):
    assert await db.claim_next_task() is None


async def test_claim_marks_running_and_sets_started_at(db):
    task_id = await db.add_task("t", "spec")
    claimed = await db.claim_next_task()
    assert claimed["id"] == task_id
    assert claimed["status"] == "running"
    assert claimed["started_at"] is not None


async def test_two_claims_return_distinct_rows_in_order(db):
    first = await db.add_task("first", "a")
    second = await db.add_task("second", "b")
    assert (await db.claim_next_task())["id"] == first
    assert (await db.claim_next_task())["id"] == second
    assert await db.claim_next_task() is None


async def test_terminal_update_sets_finished_at(db):
    task_id = await db.add_task("t", "spec")
    await db.claim_next_task()
    await db.update_task(task_id, status="done", result="all good")
    task = await db.get_task(task_id)
    assert task["status"] == "done"
    assert task["result"] == "all good"
    assert task["finished_at"] is not None


async def test_non_terminal_update_keeps_finished_at_null(db):
    task_id = await db.add_task("t", "spec")
    await db.update_task(task_id, status="running")
    assert (await db.get_task(task_id))["finished_at"] is None


async def test_update_without_result_preserves_existing(db):
    task_id = await db.add_task("t", "spec")
    await db.update_task(task_id, status="running", result="partial")
    await db.update_task(task_id, status="cancelled")
    task = await db.get_task(task_id)
    assert task["result"] == "partial"
    assert task["status"] == "cancelled"


async def test_list_tasks_filters_by_status(db):
    await db.add_task("a", "spec")
    b = await db.add_task("b", "spec")
    await db.claim_next_task()  # a → running
    queued = await db.list_tasks(status="queued")
    assert [t["id"] for t in queued] == [b]
    assert len(await db.list_tasks()) == 2
    assert await db.count_tasks("running") == 1


async def test_task_events_round_trip(db):
    task_id = await db.add_task("t", "spec")
    await db.add_task_event(task_id, "log", "started thinking")
    await db.add_task_event(task_id, "tool_call", '{"tool": "web_search"}')
    events = await db.list_task_events(task_id)
    assert [e["kind"] for e in events] == ["log", "tool_call"]
    assert events[0]["ts"] is not None


# -- shared-connection concurrency ---------------------------------------------


async def test_concurrent_writers_and_readers_do_not_race(db):
    """Commits interleaving with another coroutine's execute→fetch gap raised
    'cannot commit transaction - SQL statements in progress' (observed live:
    a background task writing audit rows while a UI turn read history).
    db.lock must make write+commit and execute+fetch uninterruptible."""
    import asyncio

    conv = await db.create_conversation("ui")

    async def writer(i: int):
        for n in range(25):
            await db.add_audit("t", f"tool{i}", "{}", "allow", 1, f"row {n}")
            await db.add_message(conv, "user", f"msg {i}-{n}")

    async def reader():
        for _ in range(25):
            await db.get_messages(conv)
            await db.list_tasks()
            await db.get_history(conv)

    await asyncio.gather(writer(1), writer(2), reader(), reader())
    assert len(await db.get_messages(conv, limit=200)) == 50


# -- schedules ----------------------------------------------------------------


async def test_list_schedules_respects_enabled(db):
    await db.conn.execute(
        "INSERT INTO schedules (cron, prompt, enabled) VALUES ('0 8 * * *', 'briefing', 1)"
    )
    await db.conn.execute(
        "INSERT INTO schedules (cron, prompt, enabled) VALUES ('0 9 * * *', 'off', 0)"
    )
    await db.conn.commit()
    enabled = await db.list_schedules()
    assert [s["prompt"] for s in enabled] == ["briefing"]
    assert len(await db.list_schedules(enabled_only=False)) == 2


async def test_set_schedule_last_run(db):
    await db.conn.execute("INSERT INTO schedules (cron, prompt) VALUES ('* * * * *', 'p')")
    await db.conn.commit()
    row = (await db.list_schedules())[0]
    await db.set_schedule_last_run(row["id"], "2026-07-04 08:00:00")
    assert (await db.list_schedules())[0]["last_run"] == "2026-07-04 08:00:00"


# -- agent iteration budget ------------------------------------------------------


async def test_max_iterations_caps_tool_loop(db):
    # A provider that always asks for another tool call: budget 2 → capped.
    endless = [
        [ToolCall(id=f"c{i}", name="get_time", arguments="{}")] for i in range(10)
    ]
    provider = FakeProvider(endless)
    conv = await db.create_conversation("task:1")
    from tools import register_all

    register_all()
    agent = AgentCore(provider, db, conv, channel="task:1", max_iterations=2)
    reply = await agent.run_turn("loop forever")
    assert "tool-step limit" in reply
    assert len(provider.requests) == 2


# -- projects (Phase 5 orchestrator) -------------------------------------------------


async def test_project_crud_round_trip(db):
    pid = await db.add_project("Starter API", "Build a FastAPI starter", notify=0)
    project = await db.get_project(pid)
    assert project["status"] == "queued" and project["notify"] == 0

    await db.update_project(pid, status="planning", plan='{"subtasks": []}')
    project = await db.get_project(pid)
    assert project["status"] == "planning" and project["plan"] == '{"subtasks": []}'
    assert project["finished_at"] is None

    await db.update_project(pid, status="done", result="all good")
    project = await db.get_project(pid)
    assert project["result"] == "all good" and project["finished_at"] is not None


async def test_claim_next_project_orders_and_empties(db):
    assert await db.claim_next_project() is None
    first = await db.add_project("p1", "spec1")
    second = await db.add_project("p2", "spec2")
    claimed = await db.claim_next_project()
    assert claimed["id"] == first and claimed["status"] == "planning"
    assert claimed["started_at"] is not None
    claimed2 = await db.claim_next_project()
    assert claimed2["id"] == second
    assert await db.claim_next_project() is None


async def test_tasks_link_to_project(db):
    pid = await db.add_project("p", "s")
    t1 = await db.add_task("sub1", "spec", notify=0, project_id=pid)
    t2 = await db.add_task("sub2", "spec", notify=0, project_id=pid)
    await db.add_task("unrelated", "spec")
    subs = await db.list_project_tasks(pid)
    assert [t["id"] for t in subs] == [t1, t2]
    assert all(t["project_id"] == pid for t in subs)


async def test_project_id_migration_on_old_db(tmp_path):
    """A tasks table created without project_id gains the column on connect."""
    import sqlite3

    from db.database import Database

    path = tmp_path / "old.db"
    raw = sqlite3.connect(path)
    raw.execute(
        "CREATE TABLE tasks (id INTEGER PRIMARY KEY, title TEXT NOT NULL,"
        " spec TEXT NOT NULL, status TEXT DEFAULT 'queued', result TEXT,"
        " notify INTEGER DEFAULT 1, created_at TEXT, started_at TEXT, finished_at TEXT)"
    )
    raw.commit()
    raw.close()

    database = Database(path)
    await database.connect()
    try:
        pid = await database.add_project("p", "s")
        tid = await database.add_task("t", "s", project_id=pid)
        task = await database.get_task(tid)
        assert task["project_id"] == pid
    finally:
        await database.close()


async def test_count_projects(db):
    await db.add_project("a", "s")
    await db.add_project("b", "s")
    await db.claim_next_project()
    assert await db.count_projects("queued") == 1
    assert await db.count_projects("planning") == 1
