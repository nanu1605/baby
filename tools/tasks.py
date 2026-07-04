"""Background task tools: start_background_task / task_status / cancel_task.

start_background_task is gated by the safety classifier (a destructive spec
needs a confirm up front); the tools INSIDE the running task still pass the
gate individually — this is defense in depth, not the only line.
"""

from __future__ import annotations

import json

from tools.registry import tool

_pool = None  # workers.queue.WorkerPool, injected at boot
_db = None  # db.database.Database, injected at boot


def configure(pool, db) -> None:
    global _pool, _db
    _pool = pool
    _db = db


def _unavailable() -> str:
    return json.dumps({"error": "background tasks are not available"})


def _public(task: dict) -> dict:
    return {
        k: task.get(k)
        for k in ("id", "title", "status", "result", "created_at", "started_at", "finished_at")
    }


@tool
async def start_background_task(title: str, spec: str) -> str:
    """Queue a background task and return its task_id immediately."""
    if _pool is None or _db is None:
        return _unavailable()
    task_id = await _db.add_task(title, spec)
    _pool.bus.publish("task_queued", "ui", task_id=task_id, title=title)
    _pool.wake()
    return json.dumps(
        {"task_id": task_id, "status": "queued", "note": "you'll be notified when it finishes"}
    )


@tool
async def task_status(task_id: int = 0) -> str:
    """Status of one background task, or the recent task list when task_id is 0."""
    if _db is None:
        return _unavailable()
    if task_id:
        task = await _db.get_task(task_id)
        if task is None:
            return json.dumps({"error": f"no task with id {task_id}"})
        return json.dumps({"task": _public(task)}, ensure_ascii=False)
    tasks = await _db.list_tasks(limit=10)
    return json.dumps({"tasks": [_public(t) for t in tasks]}, ensure_ascii=False)


@tool
async def cancel_task(task_id: int) -> str:
    """Cancel a queued or running background task."""
    if _pool is None or _db is None:
        return _unavailable()
    ok = await _pool.cancel(task_id)
    if not ok:
        return json.dumps({"error": f"task {task_id} is not queued or running"})
    return json.dumps({"cancelled": task_id})
