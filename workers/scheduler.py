"""Cron scheduler: `schedules` DB rows + the morning briefing (spec §15).

APScheduler (3.x AsyncIOScheduler) fires each enabled row's prompt through a
fresh scheduler-channel AgentCore — the same tools, gate, and audit path as
every other surface. The briefing is agent-composed from briefing.include:
no bespoke aggregation code, just the already-tested tools. misfire grace of
one hour means an 08:00 briefing still fires when the PC wakes at 08:40.
"""

from __future__ import annotations

from datetime import datetime

from core.agent import AgentCore

_INCLUDE_TEXT = {
    "date": "today's date",
    "weather": "the current weather in {city}",
    "pending_tasks": "my pending background tasks (use task_status)",
    "headlines": "three top news headlines (use web_search)",
    "system_health": "one line on system health (use get_system_stats)",
}


class Scheduler:
    def __init__(self, *, db, bus, provider, gate, config: dict, notifier) -> None:
        self.db = db
        self.bus = bus
        self.provider = provider
        self.gate = gate
        self.config = config
        self.notifier = notifier
        self._scheduler = None

    async def start(self) -> None:
        from apscheduler.schedulers.asyncio import AsyncIOScheduler
        from apscheduler.triggers.cron import CronTrigger

        self._scheduler = AsyncIOScheduler(
            job_defaults={"misfire_grace_time": 3600, "coalesce": True, "max_instances": 1}
        )
        for row in await self.db.list_schedules():
            try:
                trigger = CronTrigger.from_crontab(row["cron"])
            except ValueError:
                self.bus.publish(
                    "status",
                    "scheduler",
                    text=f"scheduler: skipping schedule #{row['id']} — bad cron {row['cron']!r}",
                )
                continue
            self._scheduler.add_job(
                self._fire,
                trigger,
                kwargs={"schedule_id": row["id"], "prompt": row["prompt"], "spoken": False},
                id=f"schedule-{row['id']}",
            )

        briefing = self.config.get("briefing", {})
        if briefing.get("enabled", False):
            try:
                trigger = CronTrigger.from_crontab(briefing.get("cron", "0 8 * * *"))
                self._scheduler.add_job(
                    self._fire,
                    trigger,
                    kwargs={
                        "schedule_id": None,
                        "prompt": self._briefing_prompt(),
                        "spoken": True,
                    },
                    id="morning-briefing",
                )
            except ValueError:
                self.bus.publish(
                    "status", "scheduler", text="scheduler: briefing cron is invalid — skipped"
                )
        self._scheduler.start()

    async def stop(self) -> None:
        if self._scheduler is not None:
            self._scheduler.shutdown(wait=False)
            self._scheduler = None

    def jobs(self) -> list:
        return self._scheduler.get_jobs() if self._scheduler else []

    def _briefing_prompt(self) -> str:
        city = self.config.get("owner", {}).get("city", "my city")
        include = self.config.get("briefing", {}).get(
            "include", ["date", "weather", "pending_tasks", "headlines", "system_health"]
        )
        parts = [
            _INCLUDE_TEXT[item].format(city=city) for item in include if item in _INCLUDE_TEXT
        ]
        return (
            "Compose my morning briefing. Cover, in order: "
            + "; ".join(parts)
            + ". It will be read aloud — keep it under 120 words, warm, no headers or bullets."
        )

    async def _fire(self, *, schedule_id: int | None, prompt: str, spoken: bool) -> None:
        label = f"schedule #{schedule_id}" if schedule_id else "morning briefing"
        self.bus.publish("status", "scheduler", text=f"scheduler: running {label}")
        conv_id = await self.db.create_conversation("scheduler")
        agent = AgentCore(
            self.provider,
            self.db,
            conv_id,
            channel="scheduler",
            bus=self.bus,
            gate=self.gate,
            memory=None,
            suggest_next_step=False,
        )
        try:
            reply = await agent.run_turn(prompt)
        except Exception as exc:  # noqa: BLE001 — a failed job must not kill the scheduler
            self.bus.publish("error", "scheduler", text=f"scheduler: {label} failed: {exc}")
            return
        if schedule_id is not None:
            await self.db.set_schedule_last_run(
                schedule_id, datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            )
        if spoken:
            await self.notifier.announce(reply, toast_title="Baby — morning briefing")
