"""Phase 4 stage 6: cron scheduler + morning briefing (offline)."""

from __future__ import annotations

import pytest

from core.bus import EventBus
from core.safety import SafetyConfig, SafetyGate
from tests.conftest import FakeProvider
from workers.scheduler import Scheduler

pytestmark = pytest.mark.asyncio

BRIEFING_CFG = {
    "owner": {"city": "Indore"},
    "briefing": {
        "enabled": True,
        "cron": "0 8 * * *",
        "include": ["date", "weather", "pending_tasks", "headlines", "system_health"],
    },
}


class RecordingNotifier:
    def __init__(self):
        self.announced: list[str] = []

    async def announce(self, text, **kwargs):
        self.announced.append(text)


def make_scheduler(db, config=None, provider=None):
    bus = EventBus()
    return (
        Scheduler(
            db=db,
            bus=bus,
            provider=provider or FakeProvider(["scheduled reply"]),
            gate=SafetyGate(SafetyConfig(), bus),
            config=config if config is not None else BRIEFING_CFG,
            notifier=RecordingNotifier(),
        ),
        bus,
    )


async def test_db_rows_become_jobs(db):
    await db.conn.execute(
        "INSERT INTO schedules (cron, prompt, enabled) VALUES ('0 9 * * *', 'daily digest', 1)"
    )
    await db.conn.execute(
        "INSERT INTO schedules (cron, prompt, enabled) VALUES ('0 10 * * *', 'disabled', 0)"
    )
    await db.conn.commit()
    scheduler, _ = make_scheduler(db, config={})
    await scheduler.start()
    try:
        ids = [j.id for j in scheduler.jobs()]
        assert ids == ["schedule-1"]  # disabled row skipped, no briefing in config
    finally:
        await scheduler.stop()


async def test_invalid_cron_skipped_with_status(db):
    await db.conn.execute(
        "INSERT INTO schedules (cron, prompt, enabled) VALUES ('not a cron', 'broken', 1)"
    )
    await db.conn.commit()
    scheduler, bus = make_scheduler(db, config={})
    q = bus.subscribe()
    await scheduler.start()
    try:
        assert scheduler.jobs() == []
        texts = []
        while not q.empty():
            texts.append(q.get_nowait().payload.get("text", ""))
        assert any("bad cron" in t for t in texts)
    finally:
        await scheduler.stop()


async def test_briefing_job_added_when_enabled(db):
    scheduler, _ = make_scheduler(db)
    await scheduler.start()
    try:
        assert [j.id for j in scheduler.jobs()] == ["morning-briefing"]
    finally:
        await scheduler.stop()


async def test_briefing_disabled_no_job(db):
    cfg = {"briefing": {"enabled": False}}
    scheduler, _ = make_scheduler(db, config=cfg)
    await scheduler.start()
    try:
        assert scheduler.jobs() == []
    finally:
        await scheduler.stop()


async def test_briefing_prompt_covers_include_items(db):
    scheduler, _ = make_scheduler(db)
    prompt = scheduler._briefing_prompt()
    for needle in ("date", "Indore", "task_status", "headlines", "system health"):
        assert needle in prompt, needle
    assert "120 words" in prompt


async def test_fire_runs_turn_updates_last_run_and_announces(db):
    await db.conn.execute(
        "INSERT INTO schedules (cron, prompt, enabled) VALUES ('0 9 * * *', 'digest', 1)"
    )
    await db.conn.commit()
    scheduler, _ = make_scheduler(db)
    await scheduler._fire(schedule_id=1, prompt="digest", spoken=True)
    assert (await db.list_schedules())[0]["last_run"] is not None
    assert scheduler.notifier.announced == ["scheduled reply"]
    # The turn went through a scheduler-channel conversation.
    convs = await db.conn.execute("SELECT channel FROM conversations ORDER BY id DESC LIMIT 1")
    assert (await convs.fetchone())["channel"] == "scheduler"


async def test_fire_failure_publishes_error_not_raise(db):
    class Exploding(FakeProvider):
        async def chat(self, messages, tools=None, **opts):
            raise RuntimeError("model down")
            yield  # pragma: no cover

    scheduler, bus = make_scheduler(db, provider=Exploding([]))
    q = bus.subscribe()
    await scheduler._fire(schedule_id=None, prompt="briefing", spoken=True)
    kinds = []
    while not q.empty():
        kinds.append(q.get_nowait().kind)
    assert "error" in kinds
    assert scheduler.notifier.announced == []
