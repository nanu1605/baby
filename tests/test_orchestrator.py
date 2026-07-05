"""Phase 5 stage 2: orchestrator over the tasks board (offline, fake pool)."""

from __future__ import annotations

import asyncio
import json

import pytest

from core.bus import EventBus
from core.safety import SafetyClass, SafetyConfig, classify_tool
from tests.conftest import FakeProvider
from tools import projects as project_tools
from workers.orchestrator import Orchestrator, parse_plan

pytestmark = pytest.mark.asyncio

PLAN = json.dumps(
    {"subtasks": [{"title": "sub a", "spec": "do a"}, {"title": "sub b", "spec": "do b"}]}
)


class FakePool:
    def __init__(self):
        self.woken = 0
        self.cancelled: list[int] = []

    def wake(self):
        self.woken += 1

    async def cancel(self, task_id: int) -> bool:
        self.cancelled.append(task_id)
        return True


class RecordingNotifier:
    def __init__(self):
        self.announced: list[str] = []

    async def announce(self, text: str, *, toast_title: str = "Baby") -> None:
        self.announced.append(text)


def make_orch(db, script, **over):
    bus = EventBus()
    pool = FakePool()
    notifier = RecordingNotifier()
    provider = FakeProvider(script)
    config = {
        "multi_agent": {
            "max_subtasks": over.pop("max_subtasks", 4),
            "project_timeout_s": over.pop("project_timeout_s", 5),
            "plan_max_tokens": 500,
        }
    }
    orch = Orchestrator(
        db=db, bus=bus, provider=provider, pool=pool, notifier=notifier,
        config=config, poll_s=over.pop("poll_s", 0.05),
    )
    return orch, bus, pool, notifier, provider


async def wait_project(db, project_id, statuses, timeout_s=3.0):
    project = None
    for _ in range(int(timeout_s / 0.01)):
        project = await db.get_project(project_id)
        if project["status"] in statuses:
            return project
        await asyncio.sleep(0.01)
    raise AssertionError(f"project never reached {statuses}: {project['status']}")


async def complete_subtasks(db, bus, project_id, status="done", result="ok", publish=True):
    tasks = []
    for _ in range(300):
        tasks = await db.list_project_tasks(project_id)
        if tasks:
            break
        await asyncio.sleep(0.01)
    assert tasks, "subtasks never appeared"
    for task in tasks:
        await db.update_task(task["id"], status=status, result=result)
        if publish:
            bus.publish(
                "task_done", f"task:{task['id']}", task_id=task["id"],
                title=task["title"], status=status, result_summary=result,
            )
    return tasks


# -- parse_plan (pure) ---------------------------------------------------------------


async def test_parse_plan_accepts_fenced_and_wrapped_json():
    text = f"Here is the plan:\n```json\n{PLAN}\n```\nGood luck!"
    plan = parse_plan(text, 4)
    assert [s["title"] for s in plan] == ["sub a", "sub b"]


async def test_parse_plan_rejects_garbage_and_empty():
    assert parse_plan("no json here", 4) is None
    assert parse_plan('{"subtasks": []}', 4) is None
    assert parse_plan('{"subtasks": [{"title": "", "spec": "x"}]}', 4) is None
    assert parse_plan('{"subtasks": "not a list"}', 4) is None


async def test_parse_plan_caps_subtasks():
    many = json.dumps(
        {"subtasks": [{"title": f"t{i}", "spec": f"s{i}"} for i in range(9)]}
    )
    assert len(parse_plan(many, 4)) == 4


# -- project lifecycle ----------------------------------------------------------------


async def test_plan_inserts_linked_subtasks(db):
    orch, bus, pool, _, _ = make_orch(db, [PLAN, "integrated result"])
    project_id = await db.add_project("proj", "build something")
    orch.start()
    try:
        orch.wake()
        await wait_project(db, project_id, ("running", "integrating", "done"))
        tasks = await db.list_project_tasks(project_id)
        assert [t["title"] for t in tasks] == ["sub a", "sub b"]
        assert all(t["notify"] == 0 and t["project_id"] == project_id for t in tasks)
        assert all(t["spec"].startswith("[Project: proj]") for t in tasks)
        assert pool.woken >= 1
    finally:
        await orch.stop()


async def test_happy_path_integrates_and_announces(db):
    orch, bus, pool, notifier, provider = make_orch(db, [PLAN, "All parts built."])
    events = bus.subscribe()
    project_id = await db.add_project("proj", "build it")
    orch.start()
    try:
        orch.wake()
        await complete_subtasks(db, bus, project_id)
        project = await wait_project(db, project_id, ("done",))
        assert project["result"] == "All parts built."
        assert json.loads(project["plan"])["subtasks"][0]["title"] == "sub a"
        assert any("project 'proj' is done" in t for t in notifier.announced)
        kinds = []
        while not events.empty():
            kinds.append(events.get_nowait().kind)
        assert "project_started" in kinds and "project_done" in kinds
    finally:
        await orch.stop()


async def test_malformed_plan_retries_once_then_succeeds(db):
    orch, bus, _, _, provider = make_orch(db, ["not json at all", PLAN, "done."])
    project_id = await db.add_project("proj", "spec")
    orch.start()
    try:
        orch.wake()
        await complete_subtasks(db, bus, project_id)
        await wait_project(db, project_id, ("done",))
        # First plan call, corrective retry, then integration = 3 model calls.
        assert len(provider.requests) == 3
        assert provider.requests[1][-1]["role"] == "system"  # the retry nudge
    finally:
        await orch.stop()


async def test_malformed_plan_twice_fails_project(db):
    orch, _, _, notifier, _ = make_orch(db, ["garbage", "still garbage"])
    project_id = await db.add_project("proj", "spec")
    orch.start()
    try:
        orch.wake()
        project = await wait_project(db, project_id, ("failed",))
        assert "planning failed" in project["result"]
        assert any("failed" in t for t in notifier.announced)
    finally:
        await orch.stop()


async def test_poll_fallback_when_bus_event_lost(db):
    orch, bus, _, _, _ = make_orch(db, [PLAN, "integrated."])
    project_id = await db.add_project("proj", "spec")
    orch.start()
    try:
        orch.wake()
        await complete_subtasks(db, bus, project_id, publish=False)  # no bus events
        project = await wait_project(db, project_id, ("done",))
        assert project["result"] == "integrated."
    finally:
        await orch.stop()


async def test_partial_failure_still_integrates(db):
    orch, bus, _, _, provider = make_orch(db, [PLAN, "one part failed, one shipped"])
    project_id = await db.add_project("proj", "spec")
    orch.start()
    try:
        orch.wake()
        tasks = []
        for _ in range(300):
            tasks = await db.list_project_tasks(project_id)
            if tasks:
                break
            await asyncio.sleep(0.01)
        await db.update_task(tasks[0]["id"], status="done", result="built")
        await db.update_task(tasks[1]["id"], status="failed", result="broke")
        for task in tasks:
            bus.publish("task_done", f"task:{task['id']}", task_id=task["id"])
        project = await wait_project(db, project_id, ("done",))
        integration_input = provider.requests[-1][-1]["content"]
        assert "[failed]" in integration_input and "[done]" in integration_input
        assert project["status"] == "done"
    finally:
        await orch.stop()


async def test_all_subtasks_failed_fails_project_without_integration(db):
    orch, bus, _, _, provider = make_orch(db, [PLAN])
    project_id = await db.add_project("proj", "spec")
    orch.start()
    try:
        orch.wake()
        await complete_subtasks(db, bus, project_id, status="failed", result="broke")
        project = await wait_project(db, project_id, ("failed",))
        assert project["result"] == "all subtasks failed"
        assert len(provider.requests) == 1  # planning only, no integration call
    finally:
        await orch.stop()


async def test_timeout_cancels_pending_subtasks(db):
    orch, _, pool, _, _ = make_orch(db, [PLAN], project_timeout_s=0.3)
    project_id = await db.add_project("proj", "spec")
    orch.start()
    try:
        orch.wake()
        project = await wait_project(db, project_id, ("failed",), timeout_s=5.0)
        assert "timed out" in project["result"]
        assert len(pool.cancelled) == 2  # both never-finished subtasks
    finally:
        await orch.stop()


async def test_cancel_queued_project(db):
    orch, _, _, _, _ = make_orch(db, [])
    project_id = await db.add_project("proj", "spec")
    assert await orch.cancel(project_id) is True
    project = await db.get_project(project_id)
    assert project["status"] == "cancelled"
    assert await orch.cancel(project_id) is False  # already finished


async def test_cancel_running_project(db):
    orch, _, pool, _, _ = make_orch(db, [PLAN], project_timeout_s=30)
    project_id = await db.add_project("proj", "spec")
    orch.start()
    try:
        orch.wake()
        await wait_project(db, project_id, ("running",))
        for _ in range(300):  # subtasks must exist before we cancel
            if await db.list_project_tasks(project_id):
                break
            await asyncio.sleep(0.01)
        assert await orch.cancel(project_id) is True
        project = await wait_project(db, project_id, ("cancelled",))
        assert project["status"] == "cancelled"
        assert pool.cancelled  # unfinished subtasks were cancelled too
    finally:
        await orch.stop()


async def test_second_project_queues_behind_first(db):
    orch, bus, _, _, _ = make_orch(db, [PLAN, "first done", PLAN, "second done"])
    first = await db.add_project("first", "spec1")
    second = await db.add_project("second", "spec2")
    orch.start()
    try:
        orch.wake()
        await wait_project(db, first, ("running",))
        assert (await db.get_project(second))["status"] == "queued"  # runner is busy
        await complete_subtasks(db, bus, first)
        await wait_project(db, first, ("done",))
        orch.wake()
        await wait_project(db, second, ("planning", "running", "integrating", "done"))
        await complete_subtasks(db, bus, second)
        await wait_project(db, second, ("done",))
    finally:
        await orch.stop()


# -- project tools + safety ------------------------------------------------------------


async def test_project_tool_round_trip(db):
    orch, bus, _, _, _ = make_orch(db, [])
    project_tools.configure(orch, db)
    result = json.loads(await project_tools.start_project("proj", "build a thing"))
    project_id = result["project_id"]
    assert result["status"] == "queued"

    status = json.loads(await project_tools.project_status(project_id))
    assert status["project"]["title"] == "proj"
    assert status["subtasks"] == []

    listing = json.loads(await project_tools.project_status())
    assert listing["projects"][0]["id"] == project_id

    cancelled = json.loads(await project_tools.cancel_project(project_id))
    assert cancelled["cancelled"] == project_id
    assert "error" in json.loads(await project_tools.cancel_project(project_id))


async def test_start_project_gated_on_destructive_spec():
    cfg = SafetyConfig()
    verdict = classify_tool(
        "start_project", {"title": "cleanup", "spec": "delete all old downloads"}, cfg
    )
    assert verdict.klass is SafetyClass.CONFIRM
    verdict = classify_tool(
        "start_project", {"title": "api", "spec": "build a starter FastAPI app"}, cfg
    )
    assert verdict.klass is SafetyClass.ALLOW
