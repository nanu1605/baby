"""One-time P2 migration: back up baby.db, add turn-hygiene columns, quarantine
pre-existing poison, print a report.

    uv run python scripts/migrate_v2_db.py [--db baby.db] [--dry-run]

Safe by construction: backs up to ``backups/baby-<stamp>.db`` and verifies the
copy opens before touching the live file. ``--dry-run`` operates on the backup
copy only, so the live database is never modified. Idempotent — re-running just
quarantines any newly-found poison.

Note: later v2 tables (P4 ``message_vectors``, P5 ``usage_log``) need no step
here — they are ``CREATE TABLE IF NOT EXISTS`` in ``schema.sql`` and auto-create
on the next ``Database.connect()``.
"""

from __future__ import annotations

import argparse
import asyncio
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from db.database import Database  # noqa: E402


def _backup(db_path: Path) -> Path:
    backups = db_path.parent / "backups"
    backups.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    dest = backups / f"{db_path.stem}-{stamp}.db"
    # Use SQLite's backup API, not a raw file copy: in WAL mode committed rows
    # can still live in the -wal sidecar, which shutil.copy2 would miss. The
    # backup API reads a consistent view through the WAL into a standalone file.
    src = sqlite3.connect(db_path)
    try:
        src.execute("PRAGMA busy_timeout=5000")
        dst = sqlite3.connect(dest)
        try:
            with dst:
                src.backup(dst)
        finally:
            dst.close()
    finally:
        src.close()
    # Verify the copy actually opens and is readable before we trust it.
    con = sqlite3.connect(dest)
    try:
        con.execute("SELECT COUNT(*) FROM messages").fetchone()
    finally:
        con.close()
    return dest


async def _scan_and_quarantine(db: Database) -> dict:
    """Quarantine empty-content user/assistant rows; count failed/quarantined."""
    rows = await db._fetchall(
        "SELECT id, role, content, status FROM messages WHERE role IN ('user', 'assistant')"
    )
    empties = [
        r["id"]
        for r in rows
        if r["status"] == "ok" and (r["content"] is None or not str(r["content"]).strip())
    ]
    await db.quarantine_messages(empties)
    failed = await db._fetchone("SELECT COUNT(*) AS n FROM messages WHERE status = 'failed'")
    quar = await db._fetchone("SELECT COUNT(*) AS n FROM messages WHERE status = 'quarantined'")
    return {"empties": len(empties), "failed": failed["n"], "quarantined": quar["n"]}


async def _run(db_path: Path, dry_run: bool) -> None:
    backup = _backup(db_path)
    print(f"backup: {backup} (verified)")

    # On a dry run, mutate the throwaway copy so the live DB is untouched but the
    # report still reflects exactly what would happen.
    target = backup if dry_run else db_path
    db = Database(target)
    await db.connect()  # adds turn_id/status columns + reconciles incomplete turns
    try:
        report = await _scan_and_quarantine(db)
    finally:
        await db.close()

    tag = " (dry-run — live DB untouched)" if dry_run else ""
    print(f"empty user/assistant rows quarantined: {report['empties']}{tag}")
    print(f"failed rows total (incl. reconciled incomplete turns): {report['failed']}")
    print(f"quarantined rows total: {report['quarantined']}")
    print("dry-run complete." if dry_run else "migration complete.")


def main() -> None:
    parser = argparse.ArgumentParser(description="P2 DB hygiene migration")
    parser.add_argument("--db", default="baby.db")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    db_path = Path(args.db)
    if not db_path.exists():
        print(f"no database at {db_path} — nothing to migrate")
        return
    if not args.dry_run:
        print("note: stop Baby before migrating so no in-progress turn is touched.")
    asyncio.run(_run(db_path, args.dry_run))


if __name__ == "__main__":
    main()
