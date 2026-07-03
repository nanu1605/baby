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
        await self._conn.commit()

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
    ) -> list[dict]:
        """Latest messages, oldest first. roles filters in SQL so tool rows
        don't consume history slots that would then be discarded client-side."""
        query = "SELECT role, content FROM messages WHERE conversation_id = ?"
        params: list = [conversation_id]
        if roles:
            query += f" AND role IN ({','.join('?' * len(roles))})"
            params.extend(roles)
        query += " ORDER BY id DESC LIMIT ?"
        params.append(limit)
        cur = await self.conn.execute(query, params)
        rows = await cur.fetchall()
        return [{"role": r["role"], "content": r["content"]} for r in reversed(rows)]
