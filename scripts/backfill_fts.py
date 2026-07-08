"""One-time backfill of the B1 FTS5 search indexes from existing rows.

The FTS mirrors (messages_fts / tasks_fts / audit_fts) stay in sync via triggers
going forward, but a DB created before B1 holds rows the triggers never saw. This
rebuilds each external-content index from its content table with FTS5's own
'rebuild' command. Idempotent — safe to run repeatedly.

    uv run python scripts/backfill_fts.py [path/to/baby.db]
"""

from __future__ import annotations

import asyncio
import sys

from db.database import Database


async def backfill(db_path: str = "baby.db") -> None:
    db = Database(db_path)
    await db.connect()  # schema.sql (runs on connect) creates the FTS tables/triggers
    try:
        for tbl in ("messages_fts", "tasks_fts", "audit_fts"):
            async with db.lock:
                await db.conn.execute(f"INSERT INTO {tbl}({tbl}) VALUES('rebuild')")
                await db.conn.commit()
            print(f"rebuilt {tbl}")
    finally:
        await db.close()


if __name__ == "__main__":
    asyncio.run(backfill(sys.argv[1] if len(sys.argv) > 1 else "baby.db"))
