"""Memory system: e5 prefixes, vector round-trips, dedup, cadence, injection.

FakeEmbedder produces deterministic token-bag vectors (crc32 buckets,
L2-normalized): shared tokens => high cosine similarity, disjoint tokens => ~0.
That scale sits far below real e5 scores, so test stores use min_similarity=0.1;
the *floor semantics* are what's under test, not the calibrated 0.80 default.
"""

from __future__ import annotations

import json
import math
import sqlite3
import zlib

import pytest

from core.agent import AgentCore
from db.database import Database
from memory import Memory
from memory.embedder import DIMENSIONS, Embedder
from memory.extractor import FactExtractor, parse_facts
from memory.store import MemoryStore
from memory.summarizer import Summarizer
from tests.conftest import FakeProvider
from tools import memory_tools, register_all, registry
from tools.registry import tool


@tool
def note_tool(text: str) -> str:
    """Write a note (test helper that always succeeds)."""
    return f"noted:{text}"


class FakeEmbedder(Embedder):
    """Deterministic offline embedder; records every text it embeds."""

    def __init__(self) -> None:
        super().__init__("fake-model")
        self.seen: list[str] = []

    async def _embed(self, text: str) -> list[float]:
        self.seen.append(text)
        vec = [0.0] * DIMENSIONS
        for token in text.lower().split():
            vec[zlib.crc32(token.encode()) % DIMENSIONS] += 1.0
        norm = math.sqrt(sum(x * x for x in vec)) or 1.0
        return [x / norm for x in vec]


@pytest.fixture
def embedder():
    return FakeEmbedder()


@pytest.fixture
async def store(db, embedder):
    s = MemoryStore(db, embedder, k=5, min_similarity=0.1, dedup_similarity=0.9)
    await s.init()
    return s


# -- 1. platform gate ---------------------------------------------------------


async def test_vec_extension_loads(store, db):
    """sqlite-vec must load on this machine — fails loudly, never skips."""
    assert store.available is True
    cur = await db.conn.execute("SELECT name FROM sqlite_master WHERE name = 'fact_vectors'")
    assert await cur.fetchone() is not None


# -- 2. e5 prefix discipline --------------------------------------------------


async def test_query_prefix(embedder):
    await embedder.embed_query("where is my gym")
    assert embedder.seen[-1] == "query: where is my gym"


async def test_passage_prefix_on_store_and_query_prefix_on_search(store, embedder):
    await store.add_fact("gym days are Monday Wednesday Friday")
    assert embedder.seen[-1].startswith("passage: ")
    await store.search("gym days")
    assert embedder.seen[-1].startswith("query: ")


# -- 3-9. store behavior ------------------------------------------------------


async def test_add_and_recall_roundtrip(store):
    await store.add_fact("Tanishq car is a Skoda Kushaq")
    await store.add_fact("gym days are Monday Wednesday Friday")
    await store.add_fact("favorite editor is neovim")
    hits = await store.search("skoda car")
    assert hits
    assert "Skoda" in hits[0]["text"]
    assert hits[0]["similarity"] >= store.min_similarity


async def test_dedup_skips_near_duplicate(store, db):
    first = await store.add_fact("Tanishq car is a Skoda Kushaq")
    second = await store.add_fact("Tanishq car is a Skoda Kushaq")
    assert first["stored"] is True
    assert second["stored"] is False
    assert second["duplicate_of"] == first["id"]
    cur = await db.conn.execute("SELECT COUNT(*) AS n FROM facts")
    assert (await cur.fetchone())["n"] == 1


async def test_dedup_allows_distinct(store, db):
    await store.add_fact("Tanishq car is a Skoda Kushaq")
    result = await store.add_fact("favorite editor is neovim")
    assert result["stored"] is True
    cur = await db.conn.execute("SELECT COUNT(*) AS n FROM facts")
    assert (await cur.fetchone())["n"] == 2


async def test_forget(store, db):
    await store.add_fact("Tanishq car is a Skoda Kushaq")
    await store.add_fact("gym days are Monday Wednesday Friday")
    result = await store.forget("skoda car")
    assert "Skoda" in result["forgotten"]

    cur = await db.conn.execute("SELECT active FROM facts WHERE id = ?", (result["id"],))
    assert (await cur.fetchone())["active"] == 0
    cur = await db.conn.execute(
        "SELECT COUNT(*) AS n FROM fact_vectors WHERE fact_id = ?", (result["id"],)
    )
    assert (await cur.fetchone())["n"] == 0
    assert await store.search("skoda car") == []


async def test_forget_no_match(store):
    await store.add_fact("gym days are Monday Wednesday Friday")
    result = await store.forget("completely unrelated topic xyz")
    assert "error" in result


async def test_similarity_floor(store):
    await store.add_fact("Tanishq car is a Skoda Kushaq")
    assert await store.search("weather in delhi today") == []


async def test_last_used_updated_on_hit_only(store, db):
    a = await store.add_fact("Tanishq car is a Skoda Kushaq")
    b = await store.add_fact("favorite editor is neovim")
    await store.search("skoda car")
    cur = await db.conn.execute("SELECT id, last_used_at FROM facts")
    used = {r["id"]: r["last_used_at"] for r in await cur.fetchall()}
    assert used[a["id"]] is not None
    assert used[b["id"]] is None


async def test_hinglish_fact_roundtrip(store):
    await store.add_fact("mera gym Monday Wednesday Friday hai")
    hits = await store.search("gym kab hai")
    assert hits
    assert "gym Monday" in hits[0]["text"]


# -- 10. tools ----------------------------------------------------------------


async def test_memory_tools_dispatch(store):
    register_all()
    memory_tools.configure(store)
    try:
        stored = json.loads(await registry.dispatch("remember", '{"fact": "gym on Monday"}'))
        assert stored["stored"] is True
        recalled = json.loads(await registry.dispatch("recall", '{"query": "gym Monday"}'))
        assert recalled["facts"]
        gone = json.loads(await registry.dispatch("forget", '{"query": "gym Monday"}'))
        assert "forgotten" in gone
    finally:
        memory_tools.configure(None)
    err = json.loads(await registry.dispatch("recall", '{"query": "gym"}'))
    assert "error" in err


# -- 11. extractor parsing ----------------------------------------------------


def test_parse_facts():
    assert parse_facts('["a", "b"]') == ["a", "b"]
    assert parse_facts('```json\n["a"]\n```') == ["a"]
    assert parse_facts('Here are the facts:\n["a", "b"] hope that helps') == ["a", "b"]
    assert parse_facts("no json here") == []
    assert parse_facts('{"not": "a list"}') == []
    assert parse_facts('["ok", 42, "", "  fine  "]') == ["ok", "fine"]
    assert parse_facts("[broken json") == []


# -- 12-13. cadence -----------------------------------------------------------


async def _fill_messages(db, conv_id: int, n: int) -> list[int]:
    ids = []
    for i in range(n):
        role = "user" if i % 2 == 0 else "assistant"
        ids.append(await db.add_message(conv_id, role, f"message {i}"))
    return ids


async def test_summarizer_cadence(db):
    conv_id = await db.create_conversation("cli")
    provider = FakeProvider(["compact summary"])
    summarizer = Summarizer(provider, db, every=10, keep_recent=5)

    await _fill_messages(db, conv_id, 6)
    assert await summarizer.maybe_summarize(conv_id) is False
    assert provider.requests == []

    ids = await _fill_messages(db, conv_id, 4)  # now 10 fresh
    assert await summarizer.maybe_summarize(conv_id) is True
    summary, upto = await db.get_summary_state(conv_id)
    assert summary == "compact summary"
    assert upto == ids[-1] - 5  # newest keep_recent stay verbatim
    assert len(provider.requests) == 1

    # immediate re-run: only 5 fresh messages left — no-op, no extra call
    assert await summarizer.maybe_summarize(conv_id) is False
    assert len(provider.requests) == 1


async def test_extractor_cadence_and_dedup(db, store):
    conv_id = await db.create_conversation("cli")
    await store.add_fact("Tanishq gym Monday Wednesday Friday")  # pre-existing
    provider = FakeProvider(['["Tanishq gym Monday Wednesday Friday", "car is Skoda Kushaq"]'])
    extractor = FactExtractor(provider, db, store, every=20)

    await _fill_messages(db, conv_id, 10)
    assert await extractor.maybe_extract(conv_id) == 0
    assert provider.requests == []

    ids = await _fill_messages(db, conv_id, 10)
    inserted = await extractor.maybe_extract(conv_id)
    assert inserted == 1  # the gym fact deduped, the car fact inserted
    assert await db.get_extracted_upto(conv_id) == ids[-1]

    cur = await db.conn.execute("SELECT source FROM facts WHERE text LIKE '%Skoda%'")
    assert (await cur.fetchone())["source"] == "extracted"


# -- 14-17. agent integration -------------------------------------------------


def _memory(provider, db, store) -> Memory:
    return Memory(
        store=store,
        summarizer=Summarizer(provider, db),
        extractor=FactExtractor(provider, db, store),
    )


async def test_agent_injects_memory_and_trims_history(db, store):
    conv_id = await db.create_conversation("cli")
    m1 = await db.add_message(conv_id, "user", "old question")
    await db.add_message(conv_id, "assistant", "old answer")
    await db.set_summary(conv_id, "they discussed old things", m1 + 1)
    await store.add_fact("mera gym Monday Wednesday Friday hai")

    provider = FakeProvider(["sure"])
    agent = AgentCore(provider, db, conv_id, channel="cli", memory=_memory(provider, db, store))
    await agent.run_turn("gym kab hai")
    if agent.maintenance_task:
        await agent.maintenance_task

    request = provider.requests[0]
    system = request[0]
    assert system["role"] == "system"
    assert "## What Baby remembers" in system["content"]
    assert "gym Monday Wednesday Friday" in system["content"]
    assert "## Conversation so far" in system["content"]
    assert "they discussed old things" in system["content"]
    # summarized turns must not reappear verbatim
    contents = [msg["content"] for msg in request[1:]]
    assert "old question" not in contents
    assert "old answer" not in contents
    assert "gym kab hai" in contents


async def test_agent_suggestion_call(db):
    from core.providers.base import ToolCall

    conv_id = await db.create_conversation("cli")
    script = [
        [ToolCall(id="c1", name="note_tool", arguments='{"text": "x"}')],
        "did it",
        "Check the logs next",
    ]
    provider = FakeProvider(script)
    agent = AgentCore(provider, db, conv_id, channel="cli", suggest_next_step=True)
    reply = await agent.run_turn("do the thing")

    assert reply == "did it\n\nNext: Check the logs next"
    assert len(provider.requests) == 3
    assert provider.request_tools[-1] is None  # suggestion call carries no tools
    rows = await db.get_messages(conv_id, roles=("assistant",))
    assert rows == [{"role": "assistant", "content": "did it\n\nNext: Check the logs next"}]


async def test_no_suggestion_for_chat_turn(db):
    conv_id = await db.create_conversation("cli")
    provider = FakeProvider(["namaste!"])
    agent = AgentCore(provider, db, conv_id, channel="cli", suggest_next_step=True)
    reply = await agent.run_turn("kaisa hai Baby?")
    assert reply == "namaste!"
    assert len(provider.requests) == 1


class _FailingSuggestionProvider(FakeProvider):
    async def chat(self, messages, tools=None, **opts):
        if not self.script:
            raise RuntimeError("suggestion model down")
        async for chunk in super().chat(messages, tools, **opts):
            yield chunk


async def test_suggestion_failure_is_soft(db):
    from core.providers.base import ToolCall

    conv_id = await db.create_conversation("cli")
    script = [
        [ToolCall(id="c1", name="note_tool", arguments='{"text": "x"}')],
        "did it",
    ]
    provider = _FailingSuggestionProvider(script)
    agent = AgentCore(provider, db, conv_id, channel="cli", suggest_next_step=True)
    reply = await agent.run_turn("do the thing")
    assert reply == "did it"


# -- 18. fallback path --------------------------------------------------------


async def test_store_fallback_bruteforce(db, embedder, monkeypatch, tmp_path):
    monkeypatch.setattr("sqlite_vec.loadable_path", lambda: str(tmp_path / "missing_vec0.dll"))
    s = MemoryStore(db, embedder, k=5, min_similarity=0.1, dedup_similarity=0.9)
    await s.init()
    assert s.available is False

    assert (await s.add_fact("Tanishq car is a Skoda Kushaq"))["stored"] is True
    assert (await s.add_fact("Tanishq car is a Skoda Kushaq"))["stored"] is False
    hits = await s.search("skoda car")
    assert hits and "Skoda" in hits[0]["text"]
    gone = await s.forget("skoda car")
    assert "forgotten" in gone
    assert await s.search("skoda car") == []


# -- 19. migration ------------------------------------------------------------


async def test_migration_adds_watermark_columns(tmp_path):
    path = tmp_path / "old.db"
    old = sqlite3.connect(path)
    old.execute(
        "CREATE TABLE conversations (id INTEGER PRIMARY KEY, channel TEXT NOT NULL,"
        " started_at TEXT DEFAULT (datetime('now')), summary TEXT)"
    )
    old.execute("INSERT INTO conversations (channel) VALUES ('cli')")
    old.commit()
    old.close()

    database = Database(path)
    await database.connect()
    try:
        cur = await database.conn.execute("PRAGMA table_info(conversations)")
        columns = {row["name"] for row in await cur.fetchall()}
        assert {"summarized_upto", "extracted_upto"} <= columns
        summary, upto = await database.get_summary_state(1)
        assert summary is None
        assert upto == 0
    finally:
        await database.close()
