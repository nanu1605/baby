"""One-time backfill: embed existing ok messages into message_vectors (P4).

Live turns embed themselves in post-turn maintenance and the nightly reconciler
catches up, but history that predates engine: v2 was never embedded — run this
once after enabling it. Only status='ok' rows are read (P2 filter is inherent in
store.embed_new_messages), so quarantined/failed poison is never embedded.

    uv run python scripts/backfill_message_vectors.py           # baby.db
    uv run python scripts/backfill_message_vectors.py --db path

Non-destructive (INSERT-only into the vector table); the P4 DB backup taken
before the migration is still the rollback point.
"""

from __future__ import annotations

import argparse
import asyncio
from pathlib import Path

from db.database import Database
from memory.embedder import Embedder
from memory.store import MemoryStore


async def _run(db_path: Path, model: str) -> None:
    db = Database(db_path)
    await db.connect()
    try:
        embedder = Embedder(model)
        store = MemoryStore(db, embedder)
        await store.init()
        await embedder.warmup()
        total = 0
        for cid in await db.list_conversation_ids():
            n = await store.embed_new_messages(cid)
            if n:
                print(f"conversation {cid}: embedded {n} message(s)")
            total += n
        print(f"done — {total} message(s) embedded into {store._msg_table}")
    finally:
        await db.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default="baby.db", help="path to baby.db")
    parser.add_argument(
        "--model", default="intfloat/multilingual-e5-small", help="embedder model id"
    )
    args = parser.parse_args()
    asyncio.run(_run(Path(args.db), args.model))
