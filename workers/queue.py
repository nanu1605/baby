"""Background task queue: asyncio worker pool over the `tasks` table.

start_background_task() inserts a row and wakes the pool; each claimed task
runs through a FRESH AgentCore (channel task:{id}, iteration budget ~15,
no memory maintenance) so the foreground chat stays responsive and the
safety gate still guards every tool call. Progress lands in task_events via
a bus recorder; completion fires the Notifier directly (spec §15).
"""

from __future__ import annotations

import asyncio
import json

from core.agent import AgentCore

_RESULT_LINE_LIMIT = 140


def result_line(text: str) -> str:
    """First line of a reply, tightened for a toast/announcement."""
    line = " ".join(text.split())
    return line if len(line) <= _RESULT_LINE_LIMIT else line[: _RESULT_LINE_LIMIT - 1] + "…"


class WorkerPool:
    """size asyncio workers pulling queued tasks; one recorder task."""

    def __init__(
        self,
        *,
        db,
        bus,
        provider,
        gate,
        config: dict,
        notifier,
        size: int = 2,
        max_iterations: int = 15,
    ) -> None:
        self.db = db
        self.bus = bus
        self.provider = provider
        self.gate = gate
        self.config = config
        self.notifier = notifier
        self.size = size
        self.max_iterations = max_iterations
        self._wake = asyncio.Event()
        self._workers: list[asyncio.Task] = []
        self._recorder: asyncio.Task | None = None
        self._running: dict[int, asyncio.Task] = {}  # task_id → child run
        self._stopping = False

    # -- lifecycle ---------------------------------------------------------------

    def start(self) -> None:
        self._workers = [
            asyncio.create_task(self._worker(), name=f"baby-worker-{i}") for i in range(self.size)
        ]
        self._recorder = asyncio.create_task(self._record_events(), name="baby-task-recorder")

    async def stop(self) -> None:
        self._stopping = True
        for child in list(self._running.values()):
            child.cancel()
        for task in [*self._workers, self._recorder]:
            if task is not None:
                task.cancel()
        for task in [*self._workers, self._recorder]:
            if task is not None:
                try:
                    await task
                except (asyncio.CancelledError, Exception):  # noqa: BLE001
                    pass

    def wake(self) -> None:
        self._wake.set()

    def running_count(self) -> int:
        return len(self._running)

    async def cancel(self, task_id: int) -> bool:
        """Cancel a queued or running task; False if it's already finished."""
        child = self._running.get(task_id)
        if child is not None:
            child.cancel()
            return True
        task = await self.db.get_task(task_id)
        if task and task["status"] == "queued":
            await self.db.update_task(task_id, status="cancelled")
            self.bus.publish(
                "task_done", "ui", task_id=task_id, title=task["title"], status="cancelled",
                result_summary="cancelled before start",
            )
            return True
        return False

    # -- workers -----------------------------------------------------------------

    async def _worker(self) -> None:
        while not self._stopping:
            row = await self.db.claim_next_task()
            if row is None:
                try:
                    await asyncio.wait_for(self._wake.wait(), timeout=5.0)
                except TimeoutError:
                    pass
                self._wake.clear()
                continue
            task_id = int(row["id"])
            child = asyncio.create_task(self._run_task(row), name=f"baby-task-{task_id}")
            self._running[task_id] = child
            try:
                await child
            except asyncio.CancelledError:
                if self._stopping:
                    raise
            finally:
                self._running.pop(task_id, None)

    async def _run_task(self, row: dict) -> None:
        task_id, title = int(row["id"]), row["title"]
        channel = f"task:{task_id}"
        self.bus.publish("task_started", channel, task_id=task_id, title=title)
        conv_id = await self.db.create_conversation(channel)
        agent = AgentCore(
            self.provider,
            self.db,
            conv_id,
            channel=channel,
            bus=self.bus,
            gate=self.gate,
            memory=None,
            suggest_next_step=False,
            max_iterations=self.max_iterations,
        )
        status, reply = "done", ""
        try:
            reply = await agent.run_turn(row["spec"])
        except asyncio.CancelledError:
            status, reply = "cancelled", "(cancelled)"
        except Exception as exc:  # noqa: BLE001 — a task failure must not kill the worker
            status, reply = "failed", f"{type(exc).__name__}: {exc}"
        line = result_line(reply)
        await self.db.update_task(task_id, status=status, result=reply)
        await self.db.add_task_event(task_id, "done", json.dumps({"status": status}))
        self.bus.publish(
            "task_done", channel, task_id=task_id, title=title, status=status, result_summary=line
        )
        try:
            await self.notifier.task_finished(
                title=title,
                ok=(status == "done"),
                result_line=line if status != "cancelled" else "",
                notify=int(row.get("notify", 1)) if status != "cancelled" else 0,
            )
        except Exception:  # noqa: BLE001 — notification failure never fails the task
            pass

    # -- event persistence -----------------------------------------------------------

    async def _record_events(self) -> None:
        """Mirror task-channel tool activity into task_events for GET /tasks."""
        q = self.bus.subscribe()
        try:
            while True:
                event = await q.get()
                if not event.channel.startswith("task:"):
                    continue
                if event.kind not in ("tool_end", "error"):
                    continue
                task_id = int(event.channel.split(":", 1)[1])
                await self.db.add_task_event(
                    task_id,
                    "tool_call" if event.kind == "tool_end" else "error",
                    json.dumps(event.payload, ensure_ascii=False)[:2048],
                )
        finally:
            self.bus.unsubscribe(q)
