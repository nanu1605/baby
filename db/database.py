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
        # A second connection (a stray CLI boot, the migration script) must wait
        # for a writer rather than erroring "database is locked" immediately.
        await self._conn.execute("PRAGMA busy_timeout=5000")
        await self._conn.execute("PRAGMA foreign_keys=ON")
        await self._conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
        await self._migrate()
        await self._reconcile_incomplete_turns()
        await self._conn.commit()

    async def _migrate(self) -> None:
        """Add columns that post-date a DB created from an older schema.sql
        (CREATE TABLE IF NOT EXISTS never alters an existing table)."""
        cur = await self.conn.execute("PRAGMA table_info(conversations)")
        have = {row["name"] for row in await cur.fetchall()}
        for column in ("summarized_upto", "extracted_upto", "message_embedded_upto"):
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
        # P2 DB hygiene: group rows by turn and quarantine failed/poison turns.
        cur = await self.conn.execute("PRAGMA table_info(messages)")
        have = {row["name"] for row in await cur.fetchall()}
        if "turn_id" not in have:
            await self.conn.execute("ALTER TABLE messages ADD COLUMN turn_id INTEGER")
        if "status" not in have:
            # Existing rows become 'ok' (the column default) — no history lost.
            await self.conn.execute(
                "ALTER TABLE messages ADD COLUMN status TEXT DEFAULT 'ok'"
            )

    async def _reconcile_incomplete_turns(self) -> None:
        """Fail turns a hard crash left without a final assistant row (P2).

        A clean turn ends with an assistant message (reply / capped / cancelled
        marker). A process killed mid-turn leaves a user (+ maybe tool) row and
        no assistant — replaying that dangles the conversation. Runs at boot,
        before this process's own turns start. Legacy rows (turn_id IS NULL,
        pre-P2) are never touched.

        The "has a completed turn" test is correlated to conversation_id:
        turn_ids restart at 1 per conversation (next_turn_id), so a global check
        would spare a hard-killed turn whenever any OTHER conversation had a
        completed turn with the same id. A turn another live connection is
        streaming right now can be transiently flipped here, but run_turn marks
        the whole turn 'ok' again the moment it finishes (concurrency repair).
        """
        await self.conn.execute(
            "UPDATE messages SET status = 'failed' WHERE status = 'ok'"
            " AND turn_id IS NOT NULL AND turn_id NOT IN ("
            "   SELECT turn_id FROM messages m2"
            "   WHERE m2.role = 'assistant' AND m2.turn_id IS NOT NULL"
            "   AND m2.conversation_id = messages.conversation_id)"
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

    async def now(self) -> str:
        """Current UTC time in SQLite's own datetime format (P5 session marker).

        Using the DB's clock keeps `ts >= since` a plain string compare against
        usage_log.ts (both 'YYYY-MM-DD HH:MM:SS'); a Python ISO 'T' separator
        would sort wrong against the space-separated stored timestamps.
        """
        row = await self._fetchone("SELECT datetime('now') AS ts")
        return row["ts"]

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

    # -- token usage (P5 telemetry) -----------------------------------------

    async def add_usage(
        self,
        conversation_id: int,
        turn_id: int | None,
        channel: str,
        brain_tier: str | None,
        brain_model: str | None,
        tokens: dict,
    ) -> int:
        """Record one turn's aggregated token spend (prompt/completion/total)."""
        return await self._write(
            "INSERT INTO usage_log (conversation_id, turn_id, channel, brain_tier,"
            " brain_model, prompt_tokens, completion_tokens, total_tokens)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                conversation_id, turn_id, channel, brain_tier, brain_model,
                int(tokens.get("prompt", 0)), int(tokens.get("completion", 0)),
                int(tokens.get("total", 0)),
            ),
        )

    async def usage_today(self) -> dict:
        """Today's totals + per-brain breakdown (local calendar day)."""
        rows = await self._fetchall(
            "SELECT brain_tier, SUM(prompt_tokens) AS prompt,"
            " SUM(completion_tokens) AS completion, SUM(total_tokens) AS total"
            " FROM usage_log WHERE date(ts, 'localtime') = date('now', 'localtime')"
            " GROUP BY brain_tier"
        )
        return self._usage_summary(rows)

    async def usage_session(self, since_iso: str) -> dict:
        """Totals + per-brain breakdown since a process-start ISO timestamp."""
        rows = await self._fetchall(
            "SELECT brain_tier, SUM(prompt_tokens) AS prompt,"
            " SUM(completion_tokens) AS completion, SUM(total_tokens) AS total"
            " FROM usage_log WHERE ts >= ? GROUP BY brain_tier",
            (since_iso,),
        )
        return self._usage_summary(rows)

    @staticmethod
    def _usage_summary(rows) -> dict:
        """Grouped rows → {prompt, completion, total, by_brain: {tier: total}}."""
        out = {"prompt": 0, "completion": 0, "total": 0, "by_brain": {}}
        for row in rows:
            out["prompt"] += row["prompt"] or 0
            out["completion"] += row["completion"] or 0
            out["total"] += row["total"] or 0
            out["by_brain"][row["brain_tier"] or "unknown"] = row["total"] or 0
        return out

    # -- messages -----------------------------------------------------------

    async def add_message(
        self,
        conversation_id: int,
        role: str,
        content: str,
        turn_id: int | None = None,
        status: str = "ok",
    ) -> int:
        """Store one message. Agent turns pass turn_id so a failed turn can be
        quarantined atomically (P2); other callers keep the plain 'ok' default."""
        return await self._write(
            "INSERT INTO messages (conversation_id, role, content, turn_id, status)"
            " VALUES (?, ?, ?, ?, ?)",
            (conversation_id, role, content, turn_id, status),
        )

    async def next_turn_id(self, conversation_id: int) -> int:
        """The next per-conversation turn id (groups a run_turn's rows)."""
        row = await self._fetchone(
            "SELECT COALESCE(MAX(turn_id), 0) + 1 AS n FROM messages WHERE conversation_id = ?",
            (conversation_id,),
        )
        return row["n"]

    async def mark_turn(self, conversation_id: int, turn_id: int, status: str) -> None:
        """Set the status of every row of one turn (quarantine on failure)."""
        await self._write(
            "UPDATE messages SET status = ? WHERE conversation_id = ? AND turn_id = ?",
            (status, conversation_id, turn_id),
        )

    async def quarantine_messages(self, ids: list[int]) -> None:
        """Mark specific message rows quarantined (self-heal / migration)."""
        if not ids:
            return
        await self._write(
            f"UPDATE messages SET status = 'quarantined' WHERE id IN ({','.join('?' * len(ids))})",
            tuple(ids),
        )

    async def list_messages_by_status(
        self, conversation_id: int, status: str, limit: int = 200
    ) -> list[dict]:
        """Rows in one status (e.g. the UI's 'quarantined' forensic filter)."""
        rows = await self._fetchall(
            "SELECT id, role, content, turn_id, status FROM messages"
            " WHERE conversation_id = ? AND status = ? ORDER BY id DESC LIMIT ?",
            (conversation_id, status, limit),
        )
        return [dict(r) for r in reversed(rows)]

    async def get_messages(
        self,
        conversation_id: int,
        limit: int = 50,
        roles: tuple[str, ...] | None = None,
        after_id: int = 0,
    ) -> list[dict]:
        """Latest OK messages, oldest first. roles filters in SQL so tool rows
        don't consume history slots that would then be discarded client-side.
        after_id skips messages already folded into the rolling summary. Failed
        and quarantined rows never load (P2)."""
        query = "SELECT role, content FROM messages WHERE conversation_id = ? AND status = 'ok'"
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
        """All matching OK messages with ids, oldest first — watermark scans."""
        rows = await self._fetchall(
            "SELECT id, role, content FROM messages WHERE conversation_id = ?"
            f" AND id > ? AND status = 'ok' AND role IN ({','.join('?' * len(roles))})"
            " ORDER BY id",
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

    async def get_message_embedded_upto(self, conversation_id: int) -> int:
        row = await self._fetchone(
            "SELECT message_embedded_upto FROM conversations WHERE id = ?",
            (conversation_id,),
        )
        return (row["message_embedded_upto"] or 0) if row else 0

    async def set_message_embedded_upto(self, conversation_id: int, upto: int) -> None:
        await self._write(
            "UPDATE conversations SET message_embedded_upto = ? WHERE id = ?",
            (upto, conversation_id),
        )

    async def list_conversation_ids(self) -> list[int]:
        """All conversation ids, oldest first — the nightly embed reconciler."""
        rows = await self._fetchall("SELECT id FROM conversations ORDER BY id")
        return [r["id"] for r in rows]

    async def first_incomplete_message(
        self, conversation_id: int, after_id: int, up_to_id: int
    ) -> int | None:
        """Smallest user/assistant message id in (after_id, up_to_id] not yet
        'ok'. The embed watermark stops just before it so a row that is
        transiently 'failed' at scan time (a boot reconcile) and later repaired
        to 'ok' is still picked up on a later pass instead of being skipped."""
        row = await self._fetchone(
            "SELECT MIN(id) AS m FROM messages WHERE conversation_id = ?"
            " AND id > ? AND id <= ? AND status != 'ok'"
            " AND role IN ('user', 'assistant')",
            (conversation_id, after_id, up_to_id),
        )
        return row["m"] if row and row["m"] is not None else None

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
            " AND status = 'ok' ORDER BY id DESC LIMIT ?",
            (conversation_id, limit),
        )
        return [
            {"role": r["role"], "content": r["content"], "created_at": r["created_at"]}
            for r in reversed(rows)
        ]
