"""Async SQLite wrapper: WAL mode, schema bootstrap, conversation/message CRUD.

Concurrency: the whole app shares ONE aiosqlite connection (UI turn, voice
turn, task workers, orchestrator, memory maintenance). aiosqlite serializes
individual statements, but every read has an await gap between execute() and
fetchall() — a commit() from another coroutine landing in that gap raises
"cannot commit transaction - SQL statements in progress" (observed live while
a background task ran during an E2E battery turn). self.lock closes the gap:
every execute→fetch→commit sequence holds it. MemoryStore shares the same
lock for its direct connection access.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import aiosqlite

SCHEMA_PATH = Path(__file__).parent / "schema.sql"


class Database:
    """Single-file SQLite store for all Baby state."""

    def __init__(self, path: str | Path = "baby.db") -> None:
        self.path = Path(path)
        self._conn: aiosqlite.Connection | None = None
        # NOT re-entrant: a method holding it must never call another that
        # takes it. connect()/_migrate() run unlocked (serial boot).
        self.lock = asyncio.Lock()

    @property
    def conn(self) -> aiosqlite.Connection:
        if self._conn is None:
            raise RuntimeError("Database not connected — call connect() first")
        return self._conn

    async def connect(self) -> None:
        self._conn = await aiosqlite.connect(self.path)
        self._conn.row_factory = aiosqlite.Row
        await self._conn.execute("PRAGMA journal_mode=WAL")
        await self._conn.execute("PRAGMA foreign_keys=ON")
        await self._conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
        await self._migrate()
        await self._conn.commit()

    async def _migrate(self) -> None:
        """Add columns that post-date a DB created from an older schema.sql
        (CREATE TABLE IF NOT EXISTS never alters an existing table)."""
        cur = await self.conn.execute("PRAGMA table_info(conversations)")
        have = {row["name"] for row in await cur.fetchall()}
        for column in ("summarized_upto", "extracted_upto"):
            if column not in have:
                await self.conn.execute(
                    f"ALTER TABLE conversations ADD COLUMN {column} INTEGER DEFAULT 0"
                )
        cur = await self.conn.execute("PRAGMA table_info(tasks)")
        have = {row["name"] for row in await cur.fetchall()}
        if "project_id" not in have:
            await self.conn.execute(
                "ALTER TABLE tasks ADD COLUMN project_id INTEGER REFERENCES projects(id)"
            )

    async def close(self) -> None:
        if self._conn is not None:
            await self._conn.close()
            self._conn = None

    # -- locked primitives ----------------------------------------------------

    async def _write(self, sql: str, params: tuple | list = ()) -> int:
        """INSERT/UPDATE + commit as one uninterruptible sequence."""
        async with self.lock:
            cur = await self.conn.execute(sql, params)
            await self.conn.commit()
            return cur.lastrowid

    async def _write_returning(self, sql: str, params: tuple | list = ()):
        """UPDATE … RETURNING + commit (atomic claim pattern)."""
        async with self.lock:
            cur = await self.conn.execute(sql, params)
            row = await cur.fetchone()
            await self.conn.commit()
            return row

    async def _fetchone(self, sql: str, params: tuple | list = ()):
        async with self.lock:
            cur = await self.conn.execute(sql, params)
            return await cur.fetchone()

    async def _fetchall(self, sql: str, params: tuple | list = ()):
        async with self.lock:
            cur = await self.conn.execute(sql, params)
            return await cur.fetchall()

    # -- conversations ------------------------------------------------------

    async def create_conversation(self, channel: str) -> int:
        return await self._write(
            "INSERT INTO conversations (channel) VALUES (?)", (channel,)
        )

    async def latest_conversation(self, channel: str) -> int | None:
        row = await self._fetchone(
            "SELECT id FROM conversations WHERE channel = ? ORDER BY id DESC LIMIT 1",
            (channel,),
        )
        return row["id"] if row else None

    # -- audit ---------------------------------------------------------------

    async def add_audit(
        self,
        channel: str,
        tool: str,
        args: str,
        safety_class: str,
        approved: int,
        result_summary: str,
    ) -> int:
        return await self._write(
            "INSERT INTO audit_log (channel, tool, args, safety_class, approved,"
            " result_summary) VALUES (?, ?, ?, ?, ?, ?)",
            (channel, tool, args, safety_class, approved, result_summary),
        )

    # -- messages -----------------------------------------------------------

    async def add_message(self, conversation_id: int, role: str, content: str) -> int:
        return await self._write(
            "INSERT INTO messages (conversation_id, role, content) VALUES (?, ?, ?)",
            (conversation_id, role, content),
        )

    async def get_messages(
        self,
        conversation_id: int,
        limit: int = 50,
        roles: tuple[str, ...] | None = None,
        after_id: int = 0,
    ) -> list[dict]:
        """Latest messages, oldest first. roles filters in SQL so tool rows
        don't consume history slots that would then be discarded client-side.
        after_id skips messages already folded into the rolling summary."""
        query = "SELECT role, content FROM messages WHERE conversation_id = ?"
        params: list = [conversation_id]
        if roles:
            query += f" AND role IN ({','.join('?' * len(roles))})"
            params.extend(roles)
        if after_id:
            query += " AND id > ?"
            params.append(after_id)
        query += " ORDER BY id DESC LIMIT ?"
        params.append(limit)
        rows = await self._fetchall(query, params)
        return [{"role": r["role"], "content": r["content"]} for r in reversed(rows)]

    async def messages_since(
        self,
        conversation_id: int,
        after_id: int,
        roles: tuple[str, ...] = ("user", "assistant"),
    ) -> list[dict]:
        """All matching messages with ids, oldest first — watermark scans."""
        rows = await self._fetchall(
            "SELECT id, role, content FROM messages WHERE conversation_id = ?"
            f" AND id > ? AND role IN ({','.join('?' * len(roles))}) ORDER BY id",
            (conversation_id, after_id, *roles),
        )
        return [{"id": r["id"], "role": r["role"], "content": r["content"]} for r in rows]

    # -- memory bookkeeping ---------------------------------------------------

    async def get_summary_state(self, conversation_id: int) -> tuple[str | None, int]:
        row = await self._fetchone(
            "SELECT summary, summarized_upto FROM conversations WHERE id = ?",
            (conversation_id,),
        )
        if row is None:
            return None, 0
        return row["summary"], row["summarized_upto"] or 0

    async def set_summary(self, conversation_id: int, summary: str, upto: int) -> None:
        await self._write(
            "UPDATE conversations SET summary = ?, summarized_upto = ? WHERE id = ?",
            (summary, upto, conversation_id),
        )

    async def get_extracted_upto(self, conversation_id: int) -> int:
        row = await self._fetchone(
            "SELECT extracted_upto FROM conversations WHERE id = ?", (conversation_id,)
        )
        return (row["extracted_upto"] or 0) if row else 0

    async def set_extracted_upto(self, conversation_id: int, upto: int) -> None:
        await self._write(
            "UPDATE conversations SET extracted_upto = ? WHERE id = ?",
            (upto, conversation_id),
        )

    # -- background tasks -----------------------------------------------------

    async def add_task(
        self, title: str, spec: str, notify: int = 1, project_id: int | None = None
    ) -> int:
        return await self._write(
            "INSERT INTO tasks (title, spec, notify, project_id) VALUES (?, ?, ?, ?)",
            (title, spec, notify, project_id),
        )

    async def claim_next_task(self) -> dict | None:
        """Atomically claim the oldest queued task (single writer connection,
        UPDATE…RETURNING) so two workers can never grab the same row."""
        row = await self._write_returning(
            "UPDATE tasks SET status = 'running', started_at = datetime('now')"
            " WHERE id = (SELECT id FROM tasks WHERE status = 'queued' ORDER BY id LIMIT 1)"
            " RETURNING *"
        )
        return dict(row) if row else None

    async def update_task(self, task_id: int, *, status: str, result: str | None = None) -> None:
        terminal = status in ("done", "failed", "cancelled")
        await self._write(
            "UPDATE tasks SET status = ?, result = COALESCE(?, result),"
            " finished_at = CASE WHEN ? THEN datetime('now') ELSE finished_at END"
            " WHERE id = ?",
            (status, result, terminal, task_id),
        )

    async def get_task(self, task_id: int) -> dict | None:
        row = await self._fetchone("SELECT * FROM tasks WHERE id = ?", (task_id,))
        return dict(row) if row else None

    async def list_tasks(self, limit: int = 50, status: str | None = None) -> list[dict]:
        query, params = "SELECT * FROM tasks", []
        if status:
            query += " WHERE status = ?"
            params.append(status)
        query += " ORDER BY id DESC LIMIT ?"
        params.append(limit)
        return [dict(r) for r in await self._fetchall(query, params)]

    async def count_tasks(self, status: str) -> int:
        row = await self._fetchone(
            "SELECT COUNT(*) AS n FROM tasks WHERE status = ?", (status,)
        )
        return row["n"]

    async def add_task_event(self, task_id: int, kind: str, payload: str) -> int:
        return await self._write(
            "INSERT INTO task_events (task_id, kind, payload) VALUES (?, ?, ?)",
            (task_id, kind, payload),
        )

    async def list_task_events(self, task_id: int, limit: int = 100) -> list[dict]:
        rows = await self._fetchall(
            "SELECT * FROM task_events WHERE task_id = ? ORDER BY id LIMIT ?",
            (task_id, limit),
        )
        return [dict(r) for r in rows]

    # -- projects (Phase 5 orchestrator) -----------------------------------------

    async def add_project(self, title: str, spec: str, notify: int = 1) -> int:
        return await self._write(
            "INSERT INTO projects (title, spec, notify) VALUES (?, ?, ?)",
            (title, spec, notify),
        )

    async def claim_next_project(self) -> dict | None:
        """Atomically claim the oldest queued project (same pattern as tasks)."""
        row = await self._write_returning(
            "UPDATE projects SET status = 'planning', started_at = datetime('now')"
            " WHERE id = (SELECT id FROM projects WHERE status = 'queued' ORDER BY id LIMIT 1)"
            " RETURNING *"
        )
        return dict(row) if row else None

    async def update_project(
        self,
        project_id: int,
        *,
        status: str,
        plan: str | None = None,
        result: str | None = None,
    ) -> None:
        terminal = status in ("done", "failed", "cancelled")
        await self._write(
            "UPDATE projects SET status = ?, plan = COALESCE(?, plan),"
            " result = COALESCE(?, result),"
            " finished_at = CASE WHEN ? THEN datetime('now') ELSE finished_at END"
            " WHERE id = ?",
            (status, plan, result, terminal, project_id),
        )

    async def get_project(self, project_id: int) -> dict | None:
        row = await self._fetchone("SELECT * FROM projects WHERE id = ?", (project_id,))
        return dict(row) if row else None

    async def list_projects(self, limit: int = 20) -> list[dict]:
        rows = await self._fetchall(
            "SELECT * FROM projects ORDER BY id DESC LIMIT ?", (limit,)
        )
        return [dict(r) for r in rows]

    async def list_project_tasks(self, project_id: int) -> list[dict]:
        rows = await self._fetchall(
            "SELECT * FROM tasks WHERE project_id = ? ORDER BY id", (project_id,)
        )
        return [dict(r) for r in rows]

    async def count_projects(self, status: str) -> int:
        row = await self._fetchone(
            "SELECT COUNT(*) AS n FROM projects WHERE status = ?", (status,)
        )
        return row["n"]

    # -- schedules --------------------------------------------------------------

    async def list_schedules(self, enabled_only: bool = True) -> list[dict]:
        query = "SELECT * FROM schedules"
        if enabled_only:
            query += " WHERE enabled = 1"
        return [dict(r) for r in await self._fetchall(query + " ORDER BY id")]

    async def set_schedule_last_run(self, schedule_id: int, ts: str) -> None:
        await self._write(
            "UPDATE schedules SET last_run = ? WHERE id = ?", (ts, schedule_id)
        )

    async def get_history(self, conversation_id: int, limit: int = 50) -> list[dict]:
        """User/assistant messages with timestamps, oldest first — UI backfill."""
        rows = await self._fetchall(
            "SELECT role, content, created_at FROM messages"
            " WHERE conversation_id = ? AND role IN ('user', 'assistant')"
            " ORDER BY id DESC LIMIT ?",
            (conversation_id, limit),
        )
        return [
            {"role": r["role"], "content": r["content"], "created_at": r["created_at"]}
            for r in reversed(rows)
        ]
