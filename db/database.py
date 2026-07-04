"""Async SQLite wrapper: WAL mode, schema bootstrap, conversation/message CRUD."""

from __future__ import annotations

from pathlib import Path

import aiosqlite

SCHEMA_PATH = Path(__file__).parent / "schema.sql"


class Database:
    """Single-file SQLite store for all Baby state."""

    def __init__(self, path: str | Path = "baby.db") -> None:
        self.path = Path(path)
        self._conn: aiosqlite.Connection | None = None

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

    async def close(self) -> None:
        if self._conn is not None:
            await self._conn.close()
            self._conn = None

    # -- conversations ------------------------------------------------------

    async def create_conversation(self, channel: str) -> int:
        cur = await self.conn.execute("INSERT INTO conversations (channel) VALUES (?)", (channel,))
        await self.conn.commit()
        return cur.lastrowid

    async def latest_conversation(self, channel: str) -> int | None:
        cur = await self.conn.execute(
            "SELECT id FROM conversations WHERE channel = ? ORDER BY id DESC LIMIT 1",
            (channel,),
        )
        row = await cur.fetchone()
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
        cur = await self.conn.execute(
            "INSERT INTO audit_log (channel, tool, args, safety_class, approved,"
            " result_summary) VALUES (?, ?, ?, ?, ?, ?)",
            (channel, tool, args, safety_class, approved, result_summary),
        )
        await self.conn.commit()
        return cur.lastrowid

    # -- messages -----------------------------------------------------------

    async def add_message(self, conversation_id: int, role: str, content: str) -> int:
        cur = await self.conn.execute(
            "INSERT INTO messages (conversation_id, role, content) VALUES (?, ?, ?)",
            (conversation_id, role, content),
        )
        await self.conn.commit()
        return cur.lastrowid

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
        cur = await self.conn.execute(query, params)
        rows = await cur.fetchall()
        return [{"role": r["role"], "content": r["content"]} for r in reversed(rows)]

    async def messages_since(
        self,
        conversation_id: int,
        after_id: int,
        roles: tuple[str, ...] = ("user", "assistant"),
    ) -> list[dict]:
        """All matching messages with ids, oldest first — watermark scans."""
        cur = await self.conn.execute(
            "SELECT id, role, content FROM messages WHERE conversation_id = ?"
            f" AND id > ? AND role IN ({','.join('?' * len(roles))}) ORDER BY id",
            (conversation_id, after_id, *roles),
        )
        rows = await cur.fetchall()
        return [{"id": r["id"], "role": r["role"], "content": r["content"]} for r in rows]

    # -- memory bookkeeping ---------------------------------------------------

    async def get_summary_state(self, conversation_id: int) -> tuple[str | None, int]:
        cur = await self.conn.execute(
            "SELECT summary, summarized_upto FROM conversations WHERE id = ?",
            (conversation_id,),
        )
        row = await cur.fetchone()
        if row is None:
            return None, 0
        return row["summary"], row["summarized_upto"] or 0

    async def set_summary(self, conversation_id: int, summary: str, upto: int) -> None:
        await self.conn.execute(
            "UPDATE conversations SET summary = ?, summarized_upto = ? WHERE id = ?",
            (summary, upto, conversation_id),
        )
        await self.conn.commit()

    async def get_extracted_upto(self, conversation_id: int) -> int:
        cur = await self.conn.execute(
            "SELECT extracted_upto FROM conversations WHERE id = ?", (conversation_id,)
        )
        row = await cur.fetchone()
        return (row["extracted_upto"] or 0) if row else 0

    async def set_extracted_upto(self, conversation_id: int, upto: int) -> None:
        await self.conn.execute(
            "UPDATE conversations SET extracted_upto = ? WHERE id = ?",
            (upto, conversation_id),
        )
        await self.conn.commit()

    # -- background tasks -----------------------------------------------------

    async def add_task(self, title: str, spec: str, notify: int = 1) -> int:
        cur = await self.conn.execute(
            "INSERT INTO tasks (title, spec, notify) VALUES (?, ?, ?)",
            (title, spec, notify),
        )
        await self.conn.commit()
        return cur.lastrowid

    async def claim_next_task(self) -> dict | None:
        """Atomically claim the oldest queued task (single writer connection,
        UPDATE…RETURNING) so two workers can never grab the same row."""
        cur = await self.conn.execute(
            "UPDATE tasks SET status = 'running', started_at = datetime('now')"
            " WHERE id = (SELECT id FROM tasks WHERE status = 'queued' ORDER BY id LIMIT 1)"
            " RETURNING *"
        )
        row = await cur.fetchone()
        await self.conn.commit()
        return dict(row) if row else None

    async def update_task(self, task_id: int, *, status: str, result: str | None = None) -> None:
        terminal = status in ("done", "failed", "cancelled")
        await self.conn.execute(
            "UPDATE tasks SET status = ?, result = COALESCE(?, result),"
            " finished_at = CASE WHEN ? THEN datetime('now') ELSE finished_at END"
            " WHERE id = ?",
            (status, result, terminal, task_id),
        )
        await self.conn.commit()

    async def get_task(self, task_id: int) -> dict | None:
        cur = await self.conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,))
        row = await cur.fetchone()
        return dict(row) if row else None

    async def list_tasks(self, limit: int = 50, status: str | None = None) -> list[dict]:
        query, params = "SELECT * FROM tasks", []
        if status:
            query += " WHERE status = ?"
            params.append(status)
        query += " ORDER BY id DESC LIMIT ?"
        params.append(limit)
        cur = await self.conn.execute(query, params)
        return [dict(r) for r in await cur.fetchall()]

    async def count_tasks(self, status: str) -> int:
        cur = await self.conn.execute("SELECT COUNT(*) AS n FROM tasks WHERE status = ?", (status,))
        return (await cur.fetchone())["n"]

    async def add_task_event(self, task_id: int, kind: str, payload: str) -> int:
        cur = await self.conn.execute(
            "INSERT INTO task_events (task_id, kind, payload) VALUES (?, ?, ?)",
            (task_id, kind, payload),
        )
        await self.conn.commit()
        return cur.lastrowid

    async def list_task_events(self, task_id: int, limit: int = 100) -> list[dict]:
        cur = await self.conn.execute(
            "SELECT * FROM task_events WHERE task_id = ? ORDER BY id LIMIT ?",
            (task_id, limit),
        )
        return [dict(r) for r in await cur.fetchall()]

    # -- schedules --------------------------------------------------------------

    async def list_schedules(self, enabled_only: bool = True) -> list[dict]:
        query = "SELECT * FROM schedules"
        if enabled_only:
            query += " WHERE enabled = 1"
        cur = await self.conn.execute(query + " ORDER BY id")
        return [dict(r) for r in await cur.fetchall()]

    async def set_schedule_last_run(self, schedule_id: int, ts: str) -> None:
        await self.conn.execute(
            "UPDATE schedules SET last_run = ? WHERE id = ?", (ts, schedule_id)
        )
        await self.conn.commit()

    async def get_history(self, conversation_id: int, limit: int = 50) -> list[dict]:
        """User/assistant messages with timestamps, oldest first — UI backfill."""
        cur = await self.conn.execute(
            "SELECT role, content, created_at FROM messages"
            " WHERE conversation_id = ? AND role IN ('user', 'assistant')"
            " ORDER BY id DESC LIMIT ?",
            (conversation_id, limit),
        )
        rows = await cur.fetchall()
        return [
            {"role": r["role"], "content": r["content"], "created_at": r["created_at"]}
            for r in reversed(rows)
        ]
