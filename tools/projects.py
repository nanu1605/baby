"""Multi-agent project tools: start_project / project_status / cancel_project.

start_project is gated by the safety classifier exactly like
start_background_task (a destructive spec needs a confirm up front); every
tool the workers call while executing subtasks still passes the gate
individually — defense in depth.
"""

from __future__ import annotations

import json

from tools.registry import tool

_orchestrator = None  # workers.orchestrator.Orchestrator, injected at boot
_db = None  # db.database.Database, injected at boot


def configure(orchestrator, db) -> None:
    global _orchestrator, _db
    _orchestrator = orchestrator
    _db = db


def _unavailable() -> str:
    return json.dumps({"error": "multi-agent projects are not available"})


def _public(project: dict) -> dict:
    return {
        k: project.get(k)
        for k in ("id", "title", "status", "result", "created_at", "started_at", "finished_at")
    }


@tool
async def start_project(title: str, spec: str) -> str:
    """Start a multi-step project: Baby plans it, splits it into subtasks and
    runs them with background workers. Returns a project_id immediately."""
    if _orchestrator is None or _db is None:
        return _unavailable()
    project_id = await _db.add_project(title, spec)
    _orchestrator.bus.publish(
        "status", "ui", text=f"project #{project_id} queued: {title}"
    )
    _orchestrator.wake()
    return json.dumps(
        {
            "project_id": project_id,
            "status": "queued",
            "note": "you'll be notified when the whole project finishes",
        }
    )


@tool
async def project_status(project_id: int = 0) -> str:
    """Status of one project (with its subtasks), or recent projects when 0."""
    if _db is None:
        return _unavailable()
    if project_id:
        project = await _db.get_project(project_id)
        if project is None:
            return json.dumps({"error": f"no project with id {project_id}"})
        subtasks = await _db.list_project_tasks(project_id)
        return json.dumps(
            {
                "project": _public(project),
                "subtasks": [
                    {k: t.get(k) for k in ("id", "title", "status", "result")}
                    for t in subtasks
                ],
            },
            ensure_ascii=False,
        )
    projects = await _db.list_projects(limit=10)
    return json.dumps({"projects": [_public(p) for p in projects]}, ensure_ascii=False)


@tool
async def cancel_project(project_id: int) -> str:
    """Cancel a queued or running project (its unfinished subtasks too)."""
    if _orchestrator is None or _db is None:
        return _unavailable()
    ok = await _orchestrator.cancel(project_id)
    if not ok:
        return json.dumps({"error": f"project {project_id} is not queued or running"})
    return json.dumps({"cancelled": project_id})
