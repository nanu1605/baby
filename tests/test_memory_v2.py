"""Memory v2 (P4): message-vector RAG, forget-last, delete, and true-amnesia wipe.

Reuses the deterministic offline FakeEmbedder from tests.test_memory (crc32
token-bag vectors, L2-normalized): shared tokens => high cosine, so a low
min_similarity floor exercises the retrieval semantics without real e5 weights.
"""

from __future__ import annotations

import pytest

from core.agent import AgentCore
from memory import Memory
from memory.extractor import FactExtractor
from memory.store import MemoryStore
from memory.summarizer import Summarizer
from tests.conftest import FakeProvider
from tests.test_memory import FakeEmbedder


def _rag_memory(store, provider, db, *, rag_k):
    return Memory(
        store=store,
        summarizer=Summarizer(provider, db),
        extractor=FactExtractor(provider, db, store),
        rag_k=rag_k,
    )


@pytest.fixture
def embedder():
    return FakeEmbedder()


@pytest.fixture
async def mstore(db, embedder):
    s = MemoryStore(db, embedder, k=5, min_similarity=0.1, dedup_similarity=0.9)
    await s.init()
    return s


async def _add(db, conv_id, role, content, *, status="ok"):
    return await db.add_message(conv_id, role, content, turn_id=1, status=status)


# -- message-vector embedding ------------------------------------------------


async def test_embed_new_messages_advances_watermark(mstore, db):
    conv = await db.create_conversation("test")
    await _add(db, conv, "user", "my gym is on monday")
    await _add(db, conv, "assistant", "got it, monday gym noted")
    last = await _add(db, conv, "user", "also badminton on friday")

    n = await mstore.embed_new_messages(conv)
    assert n == 3
    assert await db.get_message_embedded_upto(conv) == last
    # Idempotent: a second pass embeds nothing and never double-inserts.
    assert await mstore.embed_new_messages(conv) == 0


async def test_embed_skips_failed_and_empty(mstore, db):
    conv = await db.create_conversation("test")
    await _add(db, conv, "user", "keep this one")
    await _add(db, conv, "user", "   ", status="ok")  # empty content skipped
    await _add(db, conv, "user", "poison turn", status="failed")  # never loaded

    assert await mstore.embed_new_messages(conv) == 1


async def test_search_messages_retrieves_relevant(mstore, db):
    conv = await db.create_conversation("test")
    await _add(db, conv, "user", "we compared the nexon and punch electric cars")
    await _add(db, conv, "assistant", "the weather is sunny today")
    await mstore.embed_new_messages(conv)

    hits = await mstore.search_messages("which electric cars did we compare")
    assert hits
    assert "nexon and punch electric" in hits[0]["text"]
    assert hits[0]["conversation_id"] == conv


async def test_search_messages_excludes_current_window(mstore, db):
    past = await db.create_conversation("test")
    pid = await _add(db, past, "user", "electric cars nexon punch comparison")
    current = await db.create_conversation("test")
    cid = await _add(db, current, "user", "electric cars nexon punch again today")
    await mstore.embed_new_messages(past)
    await mstore.embed_new_messages(current)

    # Exclude the whole current conversation: only the past hit stays.
    hits = await mstore.search_messages(
        "electric cars comparison", exclude_conversation=current
    )
    ids = {h["id"] for h in hits}
    assert pid in ids
    assert cid not in ids


async def test_embed_watermark_holds_before_failed_row(mstore, db):
    """A transiently-failed row must not be skipped: the watermark holds just
    before it so a later repair-to-ok still gets embedded (review #2)."""
    conv = await db.create_conversation("test")
    await _add(db, conv, "user", "first ok row")
    fail_id = await _add(db, conv, "user", "temporarily failed row", status="failed")
    await _add(db, conv, "user", "later ok row")

    await mstore.embed_new_messages(conv)  # embeds the two ok rows, holds at fail-1
    assert await db.get_message_embedded_upto(conv) == fail_id - 1

    # The row is repaired to ok and a later pass now embeds it (the later row is
    # already embedded, so only the repaired one is newly inserted).
    await db._write("UPDATE messages SET status='ok' WHERE id=?", (fail_id,))
    assert await mstore.embed_new_messages(conv) == 1  # the repaired row
    assert await db.get_message_embedded_upto(conv) == fail_id + 1
    hits = await mstore.search_messages("temporarily failed row", exclude_conversation=999)
    assert any("temporarily failed" in h["text"] for h in hits)


async def test_search_messages_respects_floor(mstore, db):
    conv = await db.create_conversation("test")
    await _add(db, conv, "user", "completely unrelated aardvark xylophone")
    await mstore.embed_new_messages(conv)
    assert await mstore.search_messages("quantum banana helicopter") == []


# -- forget-last / delete-fact ------------------------------------------------


async def test_forget_last_deactivates_newest(mstore):
    await mstore.add_fact("owner likes tea")
    await mstore.add_fact("owner lives in Indore")
    result = await mstore.forget_last()
    assert result == {"forgotten": ["owner lives in Indore"]}
    remaining = [f["text"] for f in await mstore.search("tell me about the owner")]
    assert "owner lives in Indore" not in remaining
    assert "owner likes tea" in remaining


async def test_forget_last_empty_store(mstore):
    assert "error" in await mstore.forget_last()


async def test_delete_fact_removes_row_and_vector(mstore, db):
    stored = await mstore.add_fact("secret project codename bluebird")
    fact_id = stored["id"]
    result = await mstore.delete_fact(fact_id)
    assert result["deleted"] == fact_id
    # gone from facts AND its vector row — search can never surface it again.
    assert await mstore.search("bluebird codename") == []
    cur = await db.conn.execute(
        f"SELECT COUNT(*) AS n FROM {mstore._fact_table} WHERE fact_id = ?", (fact_id,)
    )
    assert (await cur.fetchone())["n"] == 0


async def test_delete_fact_unknown_id(mstore):
    assert "error" in await mstore.delete_fact(99999)


# -- wipe: true amnesia + reconciler guard -----------------------------------


async def test_wipe_all_true_amnesia(mstore, db):
    conv = await db.create_conversation("test")
    await _add(db, conv, "user", "remember the gym is monday")
    await db.set_summary(conv, "owner discussed gym schedule", 1)
    await mstore.add_fact("gym is on monday")
    await mstore.embed_new_messages(conv)

    counts = await mstore.wipe_all()
    assert counts["facts"] >= 1 and counts["messages"] >= 1

    # Everything conversational is gone.
    assert await mstore.search("gym") == []
    assert await mstore.search_messages("gym") == []
    assert await db.get_messages(conv, 50) == []
    summary, upto = await db.get_summary_state(conv)
    assert summary is None and upto == 0
    for table in (mstore._fact_table, mstore._msg_table):
        cur = await db.conn.execute(f"SELECT COUNT(*) AS n FROM {table}")
        assert (await cur.fetchone())["n"] == 0


async def test_wipe_then_reconcile_reembeds_nothing(mstore, db):
    """The nightly reconciler must never resurrect pre-wipe content: raw turns
    are gone and the embed watermark is reset, so a forced pass embeds zero."""
    conv = await db.create_conversation("test")
    await _add(db, conv, "user", "electric cars nexon punch comparison")
    await mstore.embed_new_messages(conv)
    await mstore.wipe_all()

    assert await db.get_message_embedded_upto(conv) == 0
    # Simulate the nightly reconciler over every conversation.
    reembedded = 0
    for cid in await db.list_conversation_ids():
        reembedded += await mstore.embed_new_messages(cid)
    assert reembedded == 0
    assert await mstore.search_messages("electric cars") == []


async def test_delete_conversation_purges_rows_vectors_and_search(mstore, db):
    """v5 scoped delete: a deleted chat leaves NOTHING that could resurface it —
    not the message rows, not the FTS mirror, not the vec0 vectors, not RAG, and
    not the conversation row. Proves the explicit message_vectors purge (the
    rowid-reuse fix) on both search paths."""
    conv = await db.create_conversation("ui")
    await _add(db, conv, "user", "we compared the nexon and punch electric cars")
    await _add(db, conv, "assistant", "the nexon won on range")
    await mstore.embed_new_messages(conv)

    # Pre-conditions: findable via FTS and vector RAG, and vectors exist.
    assert await db.search_messages_fts("nexon")
    assert await mstore.search_messages("nexon electric cars")
    cur = await db.conn.execute(f"SELECT COUNT(*) AS n FROM {mstore._msg_table}")
    assert (await cur.fetchone())["n"] >= 1

    result = await mstore.delete_conversation(conv)
    assert result["deleted"] == conv

    # Post: every trace is gone.
    assert await db.get_history(conv, 50) == []
    assert await db.search_messages_fts("nexon") == []
    assert await mstore.search_messages("nexon electric cars") == []
    cur = await db.conn.execute(f"SELECT COUNT(*) AS n FROM {mstore._msg_table}")
    assert (await cur.fetchone())["n"] == 0
    assert await db.get_conversation_meta(conv) is None


# -- P4c: agent-level RAG injection + live embedding --------------------------


async def test_agent_injects_past_context(mstore, db):
    past = await db.create_conversation("cli")
    await _add(db, past, "user", "we compared the nexon and punch electric cars")
    await mstore.embed_new_messages(past)

    provider = FakeProvider(["sure"])
    agent = AgentCore(
        provider, db, await db.create_conversation("cli"),
        channel="cli", memory=_rag_memory(mstore, provider, db, rag_k=4),
    )
    await agent.run_turn("which electric cars did we compare")
    if agent.maintenance_task:
        await agent.maintenance_task

    system = provider.requests[0][0]["content"]
    assert "## Relevant past context" in system
    assert "nexon and punch electric" in system


async def test_maintenance_embeds_turn_messages(mstore, db):
    provider = FakeProvider(["noted"])
    conv = await db.create_conversation("cli")
    agent = AgentCore(
        provider, db, conv, channel="cli",
        memory=_rag_memory(mstore, provider, db, rag_k=4),
    )
    await agent.run_turn("remember the alpha protocol")
    if agent.maintenance_task:
        await agent.maintenance_task

    assert await db.get_message_embedded_upto(conv) > 0  # live-embedded
    hits = await mstore.search_messages("alpha protocol", exclude_conversation=999)
    assert any("alpha protocol" in h["text"] for h in hits)


async def test_engine_v1_skips_rag(mstore, db):
    past = await db.create_conversation("cli")
    await _add(db, past, "user", "nexon punch electric cars comparison")
    await mstore.embed_new_messages(past)

    provider = FakeProvider(["sure"])
    conv = await db.create_conversation("cli")
    agent = AgentCore(
        provider, db, conv, channel="cli",
        memory=_rag_memory(mstore, provider, db, rag_k=0),  # engine v1
    )
    await agent.run_turn("electric cars")
    if agent.maintenance_task:
        await agent.maintenance_task

    assert "Relevant past context" not in provider.requests[0][0]["content"]
    assert await db.get_message_embedded_upto(conv) == 0  # v1 embeds nothing
