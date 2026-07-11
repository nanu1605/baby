"""Long-term facts: sqlite-vec KNN over e5 embeddings in baby.db.

vec0 quirks that shape this module (see DECISIONS.md):
- The extension loads per-connection, so the fact_vectors DDL lives here,
  not in schema.sql — the rest of the app never needs vec0.
- A KNN MATCH runs before any JOIN/WHERE on other tables, so `active = 1`
  cannot filter the KNN pass. search() over-fetches then join-filters.
- Forgotten facts KEEP their vector row: dedup must be able to see them,
  or the background extractor re-inserts a fact the user just forgot
  (observed live). An explicit re-remember reactivates the fact; an
  extracted near-match of a forgotten fact is silently dropped.
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
            # Conversation-RAG (P4): the same vec0 machinery over past messages.
            await conn.execute(
                "CREATE VIRTUAL TABLE IF NOT EXISTS message_vectors USING vec0("
                f"message_id INTEGER PRIMARY KEY, embedding float[{DIMENSIONS}] "
                "distance_metric=cosine)"
            )
            self.available = True
        except (aiosqlite.OperationalError, OSError, ImportError, AttributeError):
            self.available = False
            await conn.execute(
                "CREATE TABLE IF NOT EXISTS fact_embeddings("
                "fact_id INTEGER PRIMARY KEY, embedding BLOB NOT NULL)"
            )
            await conn.execute(
                "CREATE TABLE IF NOT EXISTS message_embeddings("
                "message_id INTEGER PRIMARY KEY, embedding BLOB NOT NULL)"
            )
        await conn.commit()

    @property
    def _fact_table(self) -> str:
        return "fact_vectors" if self.available else "fact_embeddings"

    @property
    def _msg_table(self) -> str:
        return "message_vectors" if self.available else "message_embeddings"

    # -- internal KNN ---------------------------------------------------------

    async def _knn(
        self, vector: list[float], n: int, *, vtable: str, btable: str, id_col: str
    ) -> list[tuple[int, float]]:
        """(id, cosine similarity) pairs, best first — over any vec0/BLOB pair."""
        if n <= 0:
            return []
        if self.available:
            async with self.db.lock:
                cur = await self.db.conn.execute(
                    f"SELECT {id_col}, distance FROM {vtable}"
                    " WHERE embedding MATCH ? AND k = ? ORDER BY distance",
                    (_pack(vector), n),
                )
                rows = await cur.fetchall()
            return [(r[id_col], 1.0 - r["distance"]) for r in rows]
        async with self.db.lock:
            cur = await self.db.conn.execute(f"SELECT {id_col}, embedding FROM {btable}")
            rows = await cur.fetchall()
        scored = [
            (r[id_col], sum(a * b for a, b in zip(vector, _unpack(r["embedding"]), strict=True)))
            for r in rows
        ]
        scored.sort(key=lambda item: item[1], reverse=True)
        return scored[:n]

    async def _nearest(self, vector: list[float], n: int) -> list[tuple[int, float]]:
        """(fact_id, cosine similarity) pairs, best first."""
        return await self._knn(
            vector, n, vtable="fact_vectors", btable="fact_embeddings", id_col="fact_id"
        )

    async def _nearest_messages(self, vector: list[float], n: int) -> list[tuple[int, float]]:
        """(message_id, cosine similarity) pairs, best first."""
        return await self._knn(
            vector, n, vtable="message_vectors", btable="message_embeddings", id_col="message_id"
        )

    async def _insert_vector(self, fact_id: int, vector: list[float]) -> None:
        # Caller holds db.lock (add_fact's insert block) — never take it here.
        table = "fact_vectors" if self.available else "fact_embeddings"
        await self.db.conn.execute(
            f"INSERT INTO {table}(fact_id, embedding) VALUES (?, ?)",
            (fact_id, _pack(vector)),
        )

    # -- public API -----------------------------------------------------------

    async def add_fact(self, text: str, source: str = "explicit") -> dict:
        """Store a fact unless a near-duplicate (passage-vs-passage) exists.

        Deduplication is source-aware around forgotten (active=0) facts:
        an explicit "remember" of a forgotten fact reactivates it (the user
        changed their mind); an extracted near-match of a forgotten fact is
        dropped (the extractor must never resurrect what the user forgot —
        blocked at the looser min_similarity, since extracted phrasing
        rarely matches the original word-for-word).
        """
        vector = await self.embedder.embed_passage(text)
        candidates = await self._nearest(vector, 3)
        if candidates:
            ids = [fid for fid, _ in candidates]
            async with self.db.lock:
                cur = await self.db.conn.execute(
                    f"SELECT id, text, active FROM facts WHERE id IN ({','.join('?' * len(ids))})",
                    ids,
                )
                rows = {r["id"]: r for r in await cur.fetchall()}
            for fid, sim in candidates:  # best-first
                row = rows.get(fid)
                if row is None:
                    continue
                if row["active"] and sim >= self.dedup_similarity:
                    return {"stored": False, "duplicate_of": fid, "existing": row["text"]}
                if not row["active"]:
                    if source == "explicit" and sim >= self.dedup_similarity:
                        async with self.db.lock:
                            await self.db.conn.execute(
                                "UPDATE facts SET active = 1, last_used_at = datetime('now')"
                                " WHERE id = ?",
                                (fid,),
                            )
                            await self.db.conn.commit()
                        return {"stored": True, "id": fid, "reactivated": True}
                    if source == "extracted" and sim >= self.min_similarity:
                        return {
                            "stored": False,
                            "reason": "matches a fact the user asked to forget",
                        }
        async with self.db.lock:
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
        async with self.db.lock:
            cur = await self.db.conn.execute(
                f"SELECT id, text FROM facts WHERE id IN ({','.join('?' * len(ids))})"
                " AND active = 1",
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
            async with self.db.lock:
                await self.db.conn.execute(
                    "UPDATE facts SET last_used_at = datetime('now')"
                    f" WHERE id IN ({','.join('?' * len(hit_ids))})",
                    hit_ids,
                )
                await self.db.conn.commit()
        return hits

    async def forget(self, query: str) -> dict:
        """Deactivate every stored fact matching the query.

        All matches above the floor go, not just the best one — paraphrased
        duplicates can slip past dedup (observed live: an extracted copy and
        the explicit original both held "gym days"), and forgetting one copy
        while its twin keeps answering betrays the user's intent. Reversible:
        rows stay with active=0, and vectors are kept on purpose so dedup can
        stop the extractor from re-inserting what was just forgotten.
        """
        matches = await self.search(query, update_last_used=False)
        if not matches:
            return {"error": "no stored fact matches that closely enough to forget"}
        ids = [m["id"] for m in matches]
        async with self.db.lock:
            await self.db.conn.execute(
                f"UPDATE facts SET active = 0 WHERE id IN ({','.join('?' * len(ids))})", ids
            )
            await self.db.conn.commit()
        return {"forgotten": [m["text"] for m in matches]}

    async def list_facts(self, limit: int = 200) -> list[dict]:
        async with self.db.lock:
            cur = await self.db.conn.execute(
                "SELECT id, text, source, created_at, last_used_at, active FROM facts"
                " ORDER BY id DESC LIMIT ?",
                (limit,),
            )
            return [dict(r) for r in await cur.fetchall()]

    async def count_active(self) -> int:
        async with self.db.lock:
            cur = await self.db.conn.execute(
                "SELECT COUNT(*) AS n FROM facts WHERE active = 1"
            )
            row = await cur.fetchone()
        return row["n"]

    # -- conversation-RAG: message vectors (P4) -------------------------------

    async def embed_new_messages(self, conversation_id: int) -> int:
        """Embed status='ok' user+assistant messages since the watermark.

        Mirrors the fact-extractor watermark: idempotent (a SELECT guard skips
        rows already embedded, so an interrupted run is safe to repeat), and
        the watermark only advances past rows we scanned. Runs off the reply's
        critical path (post-turn maintenance + nightly reconciler).
        """
        upto = await self.db.get_message_embedded_upto(conversation_id)
        rows = await self.db.messages_since(conversation_id, upto)
        if not rows:
            return 0
        embedded = 0
        for row in rows:
            text = (row["content"] or "").strip()
            if not text:
                continue
            vector = await self.embedder.embed_passage(text)
            async with self.db.lock:
                cur = await self.db.conn.execute(
                    f"SELECT 1 FROM {self._msg_table} WHERE message_id = ?", (row["id"],)
                )
                if await cur.fetchone() is None:
                    await self.db.conn.execute(
                        f"INSERT INTO {self._msg_table}(message_id, embedding) VALUES (?, ?)",
                        (row["id"], _pack(vector)),
                    )
                    embedded += 1
                await self.db.conn.commit()
        # Hold the watermark just before the first not-yet-ok row so a turn that
        # is transiently 'failed' (a second connection's boot reconcile) and
        # later repaired to 'ok' still gets embedded — the vec insert is
        # idempotency-guarded above, so re-scanning the ok tail is cheap.
        last_id = rows[-1]["id"]
        hole = await self.db.first_incomplete_message(conversation_id, upto, last_id)
        await self.db.set_message_embedded_upto(
            conversation_id, (hole - 1) if hole is not None else last_id
        )
        return embedded

    async def search_messages(
        self,
        query: str,
        k: int = 4,
        *,
        exclude_conversation: int | None = None,
    ) -> list[dict]:
        """Top-k past exchanges above the floor, best first — cross-session RAG.

        The current conversation is excluded whole (exclude_conversation): its
        content already reaches the model via raw history + the rolling summary,
        so RAG only surfaces genuinely other-session context — and never
        double-spends a summarized-but-out-of-window current-conv row.
        """
        if k <= 0:
            return []
        vector = await self.embedder.embed_query(query)
        candidates = await self._nearest_messages(vector, k * 4)
        candidates = [(mid, sim) for mid, sim in candidates if sim >= self.min_similarity]
        if not candidates:
            return []
        ids = [mid for mid, _ in candidates]
        async with self.db.lock:
            cur = await self.db.conn.execute(
                "SELECT id, conversation_id, role, content, created_at FROM messages"
                f" WHERE id IN ({','.join('?' * len(ids))}) AND status = 'ok'",
                ids,
            )
            rows = {r["id"]: r for r in await cur.fetchall()}
        hits: list[dict] = []
        for mid, sim in candidates:  # best-first
            row = rows.get(mid)
            if row is None:
                continue
            if exclude_conversation is not None and row["conversation_id"] == exclude_conversation:
                continue  # current conversation already in raw history + summary
            hits.append(
                {
                    "id": mid,
                    "conversation_id": row["conversation_id"],
                    "role": row["role"],
                    "text": row["content"],
                    "created_at": row["created_at"],
                    "similarity": round(sim, 4),
                }
            )
            if len(hits) >= k:
                break
        return hits

    # -- clear / forget / wipe (P4) -------------------------------------------

    async def forget_last(self) -> dict:
        """Deactivate the most-recently-added active fact ('forget that')."""
        async with self.db.lock:
            cur = await self.db.conn.execute(
                "SELECT id, text FROM facts WHERE active = 1 ORDER BY id DESC LIMIT 1"
            )
            row = await cur.fetchone()
            if row is None:
                return {"error": "no active fact to forget"}
            await self.db.conn.execute(
                "UPDATE facts SET active = 0 WHERE id = ?", (row["id"],)
            )
            await self.db.conn.commit()
        return {"forgotten": [row["text"]]}

    async def delete_fact(self, fact_id: int) -> dict:
        """Hard-delete a fact and its vector row (UI browser 'delete')."""
        async with self.db.lock:
            cur = await self.db.conn.execute(
                "SELECT text FROM facts WHERE id = ?", (fact_id,)
            )
            row = await cur.fetchone()
            if row is None:
                return {"error": "no such fact"}
            await self.db.conn.execute("DELETE FROM facts WHERE id = ?", (fact_id,))
            await self.db.conn.execute(
                f"DELETE FROM {self._fact_table} WHERE fact_id = ?", (fact_id,)
            )
            await self.db.conn.commit()
        return {"deleted": fact_id, "text": row["text"]}

    async def delete_conversation(self, conversation_id: int) -> dict:
        """Hard-delete one conversation and everything that could resurface it
        (v5 sidebar 'delete'): its messages (the messages_ad trigger self-purges
        the FTS mirror), their vector rows, its usage_log rows, and the
        conversation row. The vec0 message_vectors table is NOT trigger-backed,
        so the rows are deleted explicitly (mirroring delete_fact) — and because
        messages.id has no AUTOINCREMENT, a reused rowid could otherwise inherit a
        stale embedding. Collect the ids BEFORE deleting the messages. audit_log
        is retained (same policy as wipe_all)."""
        async with self.db.lock:
            cur = await self.db.conn.execute(
                "SELECT id FROM messages WHERE conversation_id = ?", (conversation_id,)
            )
            ids = [r["id"] for r in await cur.fetchall()]
            await self.db.conn.execute(
                "DELETE FROM messages WHERE conversation_id = ?", (conversation_id,)
            )
            if ids:
                placeholders = ",".join("?" * len(ids))
                await self.db.conn.execute(
                    f"DELETE FROM {self._msg_table} WHERE message_id IN ({placeholders})",
                    ids,
                )
            await self.db.conn.execute(
                "DELETE FROM usage_log WHERE conversation_id = ?", (conversation_id,)
            )
            await self.db.conn.execute(
                "DELETE FROM conversations WHERE id = ?", (conversation_id,)
            )
            await self.db.conn.commit()
        return {"deleted": conversation_id, "messages": len(ids)}

    async def wipe_all(self) -> dict:
        """True amnesia: every fact, every vector, the raw turns and the rolling
        summaries — then VACUUM. Dropping raw messages AND resetting the embed
        watermark is what stops the nightly reconciler re-embedding pre-wipe
        content; the caller (run_turn) additionally flushes the live session.
        audit_log is retained on purpose — the wipe itself is audited.
        """
        async with self.db.lock:
            cur = await self.db.conn.execute("SELECT COUNT(*) AS n FROM facts")
            fact_n = (await cur.fetchone())["n"]
            cur = await self.db.conn.execute("SELECT COUNT(*) AS n FROM messages")
            msg_n = (await cur.fetchone())["n"]
            await self.db.conn.execute("DELETE FROM facts")
            await self.db.conn.execute(f"DELETE FROM {self._fact_table}")
            await self.db.conn.execute("DELETE FROM messages")
            await self.db.conn.execute(f"DELETE FROM {self._msg_table}")
            await self.db.conn.execute(
                "UPDATE conversations SET summary = NULL, summarized_upto = 0,"
                " extracted_upto = 0, message_embedded_upto = 0"
            )
            await self.db.conn.commit()
            await self.db.conn.execute("VACUUM")
            await self.db.conn.commit()
        return {"facts": fact_n, "messages": msg_n}
