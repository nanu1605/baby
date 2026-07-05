"""Multi-agent orchestrator: heavy-brain planner over the tasks board (feature #9).

One project at a time: the orchestrator claims a queued project, asks the best
available brain (router tier_hint="best" — heavy if RAM allows, else cloud,
else daily with the denial notice) for a strict-JSON decomposition into at
most max_subtasks INDEPENDENT subtasks, writes them to the existing `tasks`
board, and lets the Phase 4 WorkerPool execute them (fresh daily-model
AgentCore per subtask, per-worker progress via task:{id} events — already
built). Completion is watched on the bus with a DB-poll fallback, then one
no-tools integration call produces the announced result. The orchestrator
itself never touches tools — workers do the acting; chains stay short
(spec §15).
"""

from __future__ import annotations

import asyncio
import json
import time

from workers.queue import result_line

_TERMINAL = ("done", "failed", "cancelled")

_PLAN_PROMPT = (
    "You are the planning brain of Baby, a personal assistant on the owner's PC. "
    "Split the project below into at most {n} INDEPENDENT subtasks that can run "
    "in parallel: no subtask may depend on another's output or touch the same "
    "files. Each subtask spec must be fully self-contained — the worker running "
    "it sees ONLY its own spec. Reply with ONLY a JSON object, no prose and no "
    'code fences: {{"subtasks": [{{"title": "...", "spec": "..."}}]}}'
)

_RETRY_PROMPT = (
    "Your reply was not the required JSON. Reply with ONLY the JSON object: "
    '{"subtasks": [{"title": "...", "spec": "..."}]} — nothing else.'
)

_INTEGRATE_PROMPT = (
    "You are Baby. The project '{title}' was split into subtasks whose outcomes "
    "are listed below. Write the final report for the owner: what was produced, "
    "where any artifacts live, and which subtasks failed (if any). Plain text, "
    "under 150 words, no headers."
)


def parse_plan(text: str, max_subtasks: int) -> list[dict] | None:
    """Extract {"subtasks": [{title, spec}...]} from model output; None if bad."""
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        return None
    try:
        data = json.loads(text[start : end + 1])
    except (ValueError, TypeError):
        return None
    raw = data.get("subtasks")
    if not isinstance(raw, list):
        return None
    subtasks: list[dict] = []
    for item in raw:
        if not isinstance(item, dict):
            return None
        title = str(item.get("title") or "").strip()
        spec = str(item.get("spec") or "").strip()
        if not title or not spec:
            return None
        subtasks.append({"title": title, "spec": spec})
    return subtasks[:max_subtasks] if subtasks else None


class Orchestrator:
    """Sequential project runner: plan → tasks board → wait → integrate → announce."""

    def __init__(
        self,
        *,
        db,
        bus,
        provider,
        pool,
        notifier,
        config: dict,
        poll_s: float = 10.0,
    ) -> None:
        cfg = config.get("multi_agent", {})
        self.db = db
        self.bus = bus
        self.provider = provider
        self.pool = pool
        self.notifier = notifier
        self.max_subtasks = int(cfg.get("max_subtasks", 4))
        self.timeout_s = float(cfg.get("project_timeout_s", 1800))
        self.plan_max_tokens = int(cfg.get("plan_max_tokens", 1500))
        self.poll_s = poll_s
        self._wake = asyncio.Event()
        self._runner: asyncio.Task | None = None
        self._running: dict[int, asyncio.Task] = {}  # project_id → child run
        self._stopping = False

    # -- lifecycle ---------------------------------------------------------------

    def start(self) -> None:
        self._runner = asyncio.create_task(self._run_loop(), name="baby-orchestrator")

    async def stop(self) -> None:
        self._stopping = True
        for child in list(self._running.values()):
            child.cancel()
        if self._runner is not None:
            self._runner.cancel()
            try:
                await self._runner
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass

    def wake(self) -> None:
        self._wake.set()

    def running_count(self) -> int:
        return len(self._running)

    async def cancel(self, project_id: int) -> bool:
        """Cancel a queued or running project; False if it's already finished."""
        child = self._running.get(project_id)
        if child is not None:
            child.cancel()
            return True
        project = await self.db.get_project(project_id)
        if project and project["status"] == "queued":
            await self.db.update_project(project_id, status="cancelled")
            self.bus.publish(
                "project_done", "orchestrator", project_id=project_id,
                title=project["title"], status="cancelled",
                result_summary="cancelled before start",
            )
            return True
        return False

    # -- runner ------------------------------------------------------------------

    async def _run_loop(self) -> None:
        while not self._stopping:
            row = await self.db.claim_next_project()
            if row is None:
                try:
                    await asyncio.wait_for(self._wake.wait(), timeout=5.0)
                except TimeoutError:
                    pass
                self._wake.clear()
                continue
            project_id = int(row["id"])
            child = asyncio.create_task(
                self._run_project(row), name=f"baby-project-{project_id}"
            )
            self._running[project_id] = child
            try:
                await child
            except asyncio.CancelledError:
                if self._stopping:
                    raise
            finally:
                self._running.pop(project_id, None)

    async def _run_project(self, row: dict) -> None:
        project_id, title = int(row["id"]), row["title"]
        notify = int(row.get("notify", 1))
        q = self.bus.subscribe()
        try:
            subtasks = await self._plan(row["spec"])
            if subtasks is None:
                await self._fail(
                    project_id, title,
                    "planning failed — the model did not produce a valid subtask list",
                    notify,
                )
                return

            await self.db.update_project(
                project_id, status="running",
                plan=json.dumps({"subtasks": subtasks}, ensure_ascii=False),
            )
            task_ids: set[int] = set()
            for subtask in subtasks:
                task_id = await self.db.add_task(
                    subtask["title"],
                    f"[Project: {title}] {subtask['spec']}",
                    notify=0,
                    project_id=project_id,
                )
                task_ids.add(task_id)
            self.bus.publish(
                "project_started", "orchestrator", project_id=project_id,
                title=title, subtasks=len(task_ids),
            )
            self.pool.wake()

            finished = await self._wait_for_tasks(q, task_ids)
            task_rows = await self.db.list_project_tasks(project_id)
            if not finished:
                for task in task_rows:
                    if task["status"] not in _TERMINAL:
                        await self.pool.cancel(int(task["id"]))
                await self._fail(
                    project_id, title, f"timed out after {self.timeout_s:.0f}s", notify
                )
                return
            if not any(task["status"] == "done" for task in task_rows):
                await self._fail(project_id, title, "all subtasks failed", notify)
                return

            await self.db.update_project(project_id, status="integrating")
            result = await self._integrate(title, task_rows)
            await self.db.update_project(project_id, status="done", result=result)
            line = result_line(result)
            self.bus.publish(
                "project_done", "orchestrator", project_id=project_id,
                title=title, status="done", result_summary=line,
            )
            if notify:
                try:
                    await self.notifier.announce(
                        f"Baby: your project '{title}' is done. {line}",
                        toast_title="Baby — project done",
                    )
                except Exception:  # noqa: BLE001 — notification failure never fails the project
                    pass
        except asyncio.CancelledError:
            for task in await self.db.list_project_tasks(project_id):
                if task["status"] not in _TERMINAL:
                    await self.pool.cancel(int(task["id"]))
            await self.db.update_project(project_id, status="cancelled")
            self.bus.publish(
                "project_done", "orchestrator", project_id=project_id,
                title=title, status="cancelled", result_summary="cancelled",
            )
        except Exception as exc:  # noqa: BLE001 — a project failure must not kill the runner
            await self._fail(project_id, title, f"{type(exc).__name__}: {exc}", notify)
        finally:
            self.bus.unsubscribe(q)

    async def _fail(self, project_id: int, title: str, reason: str, notify: int) -> None:
        await self.db.update_project(project_id, status="failed", result=reason)
        self.bus.publish(
            "project_done", "orchestrator", project_id=project_id,
            title=title, status="failed", result_summary=result_line(reason),
        )
        if notify:
            try:
                await self.notifier.announce(
                    f"Baby: your project '{title}' failed. {result_line(reason)}",
                    toast_title="Baby — project failed",
                )
            except Exception:  # noqa: BLE001
                pass

    async def _wait_for_tasks(self, q: asyncio.Queue, task_ids: set[int]) -> bool:
        """True when every subtask reached a terminal state; False on timeout.

        Primary signal: bus task_done events (matched by task_id — cancels can
        publish on other channels). Fallback: a DB poll every poll_s, because
        the bus is drop-oldest and a completion event can be lost under load.
        """
        pending = set(task_ids)
        deadline = time.monotonic() + self.timeout_s
        last_poll = time.monotonic()
        while pending:
            if time.monotonic() > deadline:
                return False
            try:
                event = await asyncio.wait_for(q.get(), timeout=1.0)
                if event.kind == "task_done":
                    task_id = int(event.payload.get("task_id", 0))
                    pending.discard(task_id)
            except TimeoutError:
                pass
            if time.monotonic() - last_poll >= self.poll_s:
                last_poll = time.monotonic()
                for task_id in list(pending):
                    task = await self.db.get_task(task_id)
                    if task is not None and task["status"] in _TERMINAL:
                        pending.discard(task_id)
        return True

    # -- model calls (no tools — the orchestrator never acts, workers do) ---------

    async def _chat(self, messages: list[dict], max_tokens: int) -> str:
        parts: list[str] = []
        async for chunk in self.provider.chat(
            messages,
            tools=None,
            tier_hint="best",
            max_tokens=max_tokens,
            reasoning_effort="none",
        ):
            if chunk.delta:
                parts.append(chunk.delta)
        return "".join(parts).strip()

    async def _plan(self, spec: str) -> list[dict] | None:
        messages = [
            {"role": "system", "content": _PLAN_PROMPT.format(n=self.max_subtasks)},
            {"role": "user", "content": spec},
        ]
        text = await self._chat(messages, self.plan_max_tokens)
        subtasks = parse_plan(text, self.max_subtasks)
        if subtasks is not None:
            return subtasks
        messages.append({"role": "assistant", "content": text or "(empty)"})
        messages.append({"role": "system", "content": _RETRY_PROMPT})
        text = await self._chat(messages, self.plan_max_tokens)
        return parse_plan(text, self.max_subtasks)

    async def _integrate(self, title: str, task_rows: list[dict]) -> str:
        lines = [
            f"- {task['title']} [{task['status']}]: {(task.get('result') or '')[:800]}"
            for task in task_rows
        ]
        messages = [
            {"role": "system", "content": _INTEGRATE_PROMPT.format(title=title)},
            {"role": "user", "content": "\n".join(lines)},
        ]
        result = await self._chat(messages, 700)
        if result:
            return result
        # Model came back empty — the raw outcome list is still a real answer.
        return "Subtask outcomes:\n" + "\n".join(lines)
