"""Phase 4 stage 3: background worker pool + task tools (offline)."""

from __future__ import annotations

import asyncio
import json

import pytest

from core.bus import EventBus
from core.providers.base import Chunk, ToolCall
from core.safety import SafetyConfig, SafetyGate
from tests.conftest import FakeProvider
from tools import register_all
from tools import tasks as tasks_tools
from workers.queue import WorkerPool, result_line

pytestmark = pytest.mark.asyncio


class RecordingNotifier:
    def __init__(self):
        self.calls: list[dict] = []

    async def task_finished(self, **kwargs):
        self.calls.append(kwargs)


class ExplodingProvider(FakeProvider):
    async def chat(self, messages, tools=None, **opts):
        raise RuntimeError("model on fire")
        yield  # pragma: no cover


class SlowProvider(FakeProvider):
    async def chat(self, messages, tools=None, **opts):
        await asyncio.sleep(30)
        yield Chunk(delta="too late", done=True)


def make_pool(db, provider, **over):
    bus = EventBus()
    gate = SafetyGate(SafetyConfig(), bus)
    notifier = RecordingNotifier()
    pool = WorkerPool(
        db=db,
        bus=bus,
        provider=provider,
        gate=gate,
        config={},
        notifier=notifier,
        size=over.pop("size", 2),
        max_iterations=over.pop("max_iterations", 15),
    )
    return pool, bus, notifier


async def wait_for_status(db, task_id, statuses, timeout_s=3.0):
    for _ in range(int(timeout_s / 0.01)):
        task = await db.get_task(task_id)
        if task["status"] in statuses:
            return task
        await asyncio.sleep(0.01)
    raise AssertionError(f"task {task_id} never reached {statuses}: {task['status']}")


async def test_task_lifecycle_done(db):
    pool, bus, notifier = make_pool(db, FakeProvider(["Research finished. EVs listed."]))
    events = bus.subscribe()
    task_id = await db.add_task("ev research", "research top 3 EVs")
    pool.start()
    pool.wake()
    try:
        task = await wait_for_status(db, task_id, ("done",))
        assert "Research finished" in task["result"]
        assert task["finished_at"] is not None
        assert notifier.calls and notifier.calls[0]["ok"] is True
        assert "Research finished" in notifier.calls[0]["result_line"]
        kinds = []
        while not events.empty():
            kinds.append(events.get_nowait().kind)
        assert "task_started" in kinds and "task_done" in kinds
    finally:
        await pool.stop()


async def test_task_failure_marks_failed_and_notifies(db):
    pool, _, notifier = make_pool(db, ExplodingProvider([]))
    task_id = await db.add_task("doomed", "this will fail")
    pool.start()
    pool.wake()
    try:
        task = await wait_for_status(db, task_id, ("failed",))
        assert "RuntimeError" in task["result"]
        assert notifier.calls[0]["ok"] is False
    finally:
        await pool.stop()


async def test_cancel_queued_task(db):
    pool, _, _ = make_pool(db, FakeProvider(["never"]))
    task_id = await db.add_task("waiting", "spec")
    # Pool not started: task stays queued; cancel flips it directly.
    assert await pool.cancel(task_id) is True
    assert (await db.get_task(task_id))["status"] == "cancelled"


async def test_cancel_running_task(db):
    pool, _, notifier = make_pool(db, SlowProvider([]))
    task_id = await db.add_task("slow", "spec")
    pool.start()
    pool.wake()
    try:
        await wait_for_status(db, task_id, ("running",))
        for _ in range(100):  # cancel() needs the child registered
            if pool.running_count():
                break
            await asyncio.sleep(0.01)
        assert await pool.cancel(task_id) is True
        task = await wait_for_status(db, task_id, ("cancelled",))
        assert task["status"] == "cancelled"
        # cancelled tasks never notify
        assert all(c.get("notify") == 0 for c in notifier.calls)
    finally:
        await pool.stop()


async def test_cancel_finished_task_returns_false(db):
    pool, _, _ = make_pool(db, FakeProvider(["ok"]))
    task_id = await db.add_task("quick", "spec")
    pool.start()
    pool.wake()
    try:
        await wait_for_status(db, task_id, ("done",))
        assert await pool.cancel(task_id) is False
    finally:
        await pool.stop()


async def test_iteration_budget_caps_background_task(db):
    register_all()
    endless = [[ToolCall(id=f"c{i}", name="get_time", arguments="{}")] for i in range(30)]
    provider = FakeProvider(endless)
    pool, _, _ = make_pool(db, provider, max_iterations=3)
    task_id = await db.add_task("looper", "loop forever")
    pool.start()
    pool.wake()
    try:
        task = await wait_for_status(db, task_id, ("done",))
        assert "tool-step limit" in task["result"]
        assert len(provider.requests) == 3
    finally:
        await pool.stop()


async def test_recorder_persists_tool_events(db):
    register_all()
    script = [
        [ToolCall(id="c1", name="get_time", arguments="{}")],
        "It is noon.",
    ]
    pool, _, _ = make_pool(db, FakeProvider(script))
    task_id = await db.add_task("timed", "what time is it")
    pool.start()
    pool.wake()
    try:
        await wait_for_status(db, task_id, ("done",))
        for _ in range(100):  # recorder consumes the bus asynchronously
            events = await db.list_task_events(task_id)
            if any(e["kind"] == "tool_call" for e in events):
                break
            await asyncio.sleep(0.01)
        kinds = [e["kind"] for e in await db.list_task_events(task_id)]
        assert "tool_call" in kinds and "done" in kinds
    finally:
        await pool.stop()


async def test_two_tasks_both_complete(db):
    pool, _, notifier = make_pool(db, FakeProvider(["reply one", "reply two"]))
    first = await db.add_task("first", "spec a")
    second = await db.add_task("second", "spec b")
    pool.start()
    pool.wake()
    try:
        await wait_for_status(db, first, ("done",))
        await wait_for_status(db, second, ("done",))
        assert len(notifier.calls) == 2
    finally:
        await pool.stop()


# -- task tools --------------------------------------------------------------------


async def test_task_tools_round_trip(db):
    pool, _, _ = make_pool(db, FakeProvider(["done"]))
    tasks_tools.configure(pool, db)
    started = json.loads(await tasks_tools.start_background_task("t", "benign research"))
    task_id = started["task_id"]
    assert started["status"] == "queued"

    one = json.loads(await tasks_tools.task_status(task_id))
    assert one["task"]["title"] == "t"
    listing = json.loads(await tasks_tools.task_status())
    assert listing["tasks"][0]["id"] == task_id

    cancelled = json.loads(await tasks_tools.cancel_task(task_id))
    assert cancelled == {"cancelled": task_id}
    assert "error" in json.loads(await tasks_tools.cancel_task(9999))


async def test_result_line_tightens_whitespace_and_length():
    assert result_line("a  b\nc") == "a b c"
    assert len(result_line("x" * 500)) == 140
