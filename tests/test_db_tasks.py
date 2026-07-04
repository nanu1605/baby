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
