"""Long-term facts: sqlite-vec KNN over e5 embeddings in baby.db.

vec0 quirks that shape this module (see DECISIONS.md):
- The extension loads per-connection, so the fact_vectors DDL lives here,
  not in schema.sql — the rest of the app never needs vec0.
- A KNN MATCH runs before any JOIN/WHERE on other tables, so `active = 1`
  cannot filter the KNN pass. forget() therefore deletes the vector row
  (the facts row stays, active=0, for audit) and search() over-fetches then
  join-filters as a backstop.
- vec0 reports cosine *distance* (1 - similarity); conversions are explicit.

If the extension can't load, the store degrades to float32 BLOBs in a plain
table with brute-force cosine — same public API, fine for v1 fact counts.
"""

from __future__ import annotations

import struct

import aiosqlite

from db.database import Database
from memory.embedder import DIMENSIONS, Embedder


def _pack(vector: list[float]) -> bytes:
    return struct.pack(f"{len(vector)}f", *vector)


def _unpack(blob: bytes) -> list[float]:
    return list(struct.unpack(f"{DIMENSIONS}f", blob))


class MemoryStore:
    """Facts CRUD + vector search over the app's single DB connection."""

    def __init__(
        self,
        db: Database,
        embedder: Embedder,
        *,
        k: int = 5,
        min_similarity: float = 0.80,
        dedup_similarity: float = 0.90,
    ) -> None:
        self.db = db
        self.embedder = embedder
        self.k = k
        self.min_similarity = min_similarity
        self.dedup_similarity = dedup_similarity
        self.available = False  # True once vec0 loads; False = brute-force fallback

    async def init(self) -> None:
        conn = self.db.conn
        try:
            import sqlite_vec

            await conn.enable_load_extension(True)
            try:
                await conn.load_extension(sqlite_vec.loadable_path())
            finally:
                await conn.enable_load_extension(False)
            await conn.execute(
                "CREATE VIRTUAL TABLE IF NOT EXISTS fact_vectors USING vec0("
                f"fact_id INTEGER PRIMARY KEY, embedding float[{DIMENSIONS}] "
                "distance_metric=cosine)"
            )
            self.available = True
        except (aiosqlite.OperationalError, OSError, ImportError, AttributeError):
            self.available = False
            await conn.execute(
                "CREATE TABLE IF NOT EXISTS fact_embeddings("
                "fact_id INTEGER PRIMARY KEY, embedding BLOB NOT NULL)"
            )
        await conn.commit()

    # -- internal KNN ---------------------------------------------------------

    async def _nearest(self, vector: list[float], n: int) -> list[tuple[int, float]]:
        """(fact_id, cosine similarity) pairs, best first."""
        if n <= 0:
            return []
        if self.available:
            cur = await self.db.conn.execute(
                "SELECT fact_id, distance FROM fact_vectors"
                " WHERE embedding MATCH ? AND k = ? ORDER BY distance",
                (_pack(vector), n),
            )
            rows = await cur.fetchall()
            return [(r["fact_id"], 1.0 - r["distance"]) for r in rows]
        cur = await self.db.conn.execute("SELECT fact_id, embedding FROM fact_embeddings")
        rows = await cur.fetchall()
        scored = [
            (r["fact_id"], sum(a * b for a, b in zip(vector, _unpack(r["embedding"]), strict=True)))
            for r in rows
        ]
        scored.sort(key=lambda item: item[1], reverse=True)
        return scored[:n]

    async def _insert_vector(self, fact_id: int, vector: list[float]) -> None:
        table = "fact_vectors" if self.available else "fact_embeddings"
        await self.db.conn.execute(
            f"INSERT INTO {table}(fact_id, embedding) VALUES (?, ?)",
            (fact_id, _pack(vector)),
        )

    async def _delete_vector(self, fact_id: int) -> None:
        table = "fact_vectors" if self.available else "fact_embeddings"
        await self.db.conn.execute(f"DELETE FROM {table} WHERE fact_id = ?", (fact_id,))

    # -- public API -----------------------------------------------------------

    async def add_fact(self, text: str, source: str = "explicit") -> dict:
        """Store a fact unless a near-duplicate (passage-vs-passage) exists."""
        vector = await self.embedder.embed_passage(text)
        top = await self._nearest(vector, 1)
        if top and top[0][1] >= self.dedup_similarity:
            dup_id = top[0][0]
            cur = await self.db.conn.execute("SELECT text FROM facts WHERE id = ?", (dup_id,))
            row = await cur.fetchone()
            return {
                "stored": False,
                "duplicate_of": dup_id,
                "existing": row["text"] if row else "",
            }
        cur = await self.db.conn.execute(
            "INSERT INTO facts (text, source) VALUES (?, ?)", (text, source)
        )
        fact_id = cur.lastrowid
        await self._insert_vector(fact_id, vector)
        await self.db.conn.commit()
        return {"stored": True, "id": fact_id}

    async def search(
        self, query: str, k: int | None = None, *, update_last_used: bool = True
    ) -> list[dict]:
        """Top-k active facts above the similarity floor, best first."""
        limit = k or self.k
        vector = await self.embedder.embed_query(query)
        candidates = await self._nearest(vector, limit * 3)
        candidates = [(fid, sim) for fid, sim in candidates if sim >= self.min_similarity]
        if not candidates:
            return []
        ids = [fid for fid, _ in candidates]
        cur = await self.db.conn.execute(
            f"SELECT id, text FROM facts WHERE id IN ({','.join('?' * len(ids))}) AND active = 1",
            ids,
        )
        texts = {r["id"]: r["text"] for r in await cur.fetchall()}
        hits = [
            {"id": fid, "text": texts[fid], "similarity": round(sim, 4)}
            for fid, sim in candidates
            if fid in texts
        ][:limit]
        if hits and update_last_used:
            hit_ids = [h["id"] for h in hits]
            await self.db.conn.execute(
                "UPDATE facts SET last_used_at = datetime('now')"
                f" WHERE id IN ({','.join('?' * len(hit_ids))})",
                hit_ids,
            )
            await self.db.conn.commit()
        return hits

    async def forget(self, query: str) -> dict:
        """Deactivate the stored fact closest to the query (audit row kept)."""
        matches = await self.search(query, k=1, update_last_used=False)
        if not matches:
            return {"error": "no stored fact matches that closely enough to forget"}
        fact = matches[0]
        await self.db.conn.execute("UPDATE facts SET active = 0 WHERE id = ?", (fact["id"],))
        await self._delete_vector(fact["id"])
        await self.db.conn.commit()
        return {"forgotten": fact["text"], "id": fact["id"]}

    async def list_facts(self, limit: int = 200) -> list[dict]:
        cur = await self.db.conn.execute(
            "SELECT id, text, source, created_at, last_used_at, active FROM facts"
            " ORDER BY id DESC LIMIT ?",
            (limit,),
        )
        return [dict(r) for r in await cur.fetchall()]

    async def count_active(self) -> int:
        cur = await self.db.conn.execute("SELECT COUNT(*) AS n FROM facts WHERE active = 1")
        row = await cur.fetchone()
        return row["n"]
