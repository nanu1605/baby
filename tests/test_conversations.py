"""H0 chat-history read spine: conversation list/detail metadata + endpoints.

The history sidebar surfaces REAL conversations only: empty rows (boot and
/conversation/new create them) are dropped, quarantined/failed turns never
count or render, archived rows hide by default, and the title is derived when
unset. These fixtures pin that contract at the DB layer plus the two additive
GET endpoints.
"""

from __future__ import annotations

import asyncio

from fastapi.testclient import TestClient

from core.agent import AgentCore
from core.bus import EventBus
from core.safety import SafetyConfig, SafetyGate
from db.database import Database
from tests.conftest import FakeProvider
from ui.server import UIContext, create_app


async def _turn(db: Database, conv: int, turn_id: int, user: str, assistant: str) -> None:
    """One completed user+assistant turn (both rows share a turn_id, status ok)."""
    await db.add_message(conv, "user", user, turn_id=turn_id)
    await db.add_message(conv, "assistant", assistant, turn_id=turn_id)


# -- list metadata -------------------------------------------------------------


async def test_list_derives_title_and_counts(db):
    conv = await db.create_conversation("ui")
    await _turn(db, conv, 1, "how do I center a div?", "use flexbox")
    await _turn(db, conv, 2, "thanks", "anytime")

    rows = await db.list_conversations()
    assert len(rows) == 1
    row = rows[0]
    assert row["id"] == conv
    assert row["channel"] == "ui"
    assert row["message_count"] == 4  # 2 user + 2 assistant
    assert row["title"] == "how do I center a div?"  # first user message
    assert row["archived"] is False
    assert row["last_message_at"] is not None


async def test_empty_conversations_excluded(db):
    # Boot / new-chat create a conversation row before any message — it must not
    # pollute the sidebar (HAVING message_count > 0).
    empty = await db.create_conversation("ui")
    used = await db.create_conversation("ui")
    await _turn(db, used, 1, "hi", "hello")

    ids = [r["id"] for r in await db.list_conversations()]
    assert used in ids
    assert empty not in ids


async def test_channel_filter_keeps_ui_only_by_default(db):
    ui_conv = await db.create_conversation("ui")
    voice_conv = await db.create_conversation("voice")
    await _turn(db, ui_conv, 1, "ui question", "ui answer")
    await _turn(db, voice_conv, 1, "voice question", "voice answer")

    ui_ids = [r["id"] for r in await db.list_conversations()]  # default channel="ui"
    assert ui_ids == [ui_conv]

    voice_ids = [r["id"] for r in await db.list_conversations(channel="voice")]
    assert voice_ids == [voice_conv]

    all_ids = {r["id"] for r in await db.list_conversations(channel=None)}
    assert {ui_conv, voice_conv} <= all_ids


async def test_pagination_and_recency_order(db):
    convs = []
    for i in range(3):
        c = await db.create_conversation("ui")
        await _turn(db, c, 1, f"q{i}", f"a{i}")
        convs.append(c)
    # Newest activity first: the last-created conversation leads.
    ordered = [r["id"] for r in await db.list_conversations()]
    assert ordered == list(reversed(convs))

    page1 = await db.list_conversations(limit=2, offset=0)
    page2 = await db.list_conversations(limit=2, offset=2)
    assert len(page1) == 2 and len(page2) == 1
    assert [r["id"] for r in page1] + [r["id"] for r in page2] == ordered


async def test_archived_hidden_unless_requested(db):
    keep = await db.create_conversation("ui")
    gone = await db.create_conversation("ui")
    await _turn(db, keep, 1, "keep me", "ok")
    await _turn(db, gone, 1, "archive me", "ok")
    # Set archived directly (the setter is H2); H0 only reads the column.
    await db._write("UPDATE conversations SET archived = 1 WHERE id = ?", (gone,))

    visible = [r["id"] for r in await db.list_conversations()]
    assert visible == [keep]

    withall = {r["id"] for r in await db.list_conversations(include_archived=True)}
    assert {keep, gone} <= withall
    archived_row = next(r for r in await db.list_conversations(include_archived=True)
                        if r["id"] == gone)
    assert archived_row["archived"] is True


# -- title derivation ----------------------------------------------------------


async def test_title_prefers_explicit_then_summary_then_first_user(db):
    # explicit title wins
    c1 = await db.create_conversation("ui")
    await _turn(db, c1, 1, "first user text", "a")
    await db._write("UPDATE conversations SET title = ? WHERE id = ?", ("My Chat", c1))
    # summary first line (no title)
    c2 = await db.create_conversation("ui")
    await _turn(db, c2, 1, "another user text", "a")
    await db._write(
        "UPDATE conversations SET summary = ? WHERE id = ?",
        ("Debugging the router\nmore detail", c2),
    )
    # fallback to first user message (no title, no summary)
    c3 = await db.create_conversation("ui")
    await _turn(db, c3, 1, "just the first message", "a")

    by_id = {r["id"]: r["title"] for r in await db.list_conversations()}
    assert by_id[c1] == "My Chat"
    assert by_id[c2] == "Debugging the router"  # first line only
    assert by_id[c3] == "just the first message"


# -- quarantine exclusion ------------------------------------------------------


async def test_quarantined_turn_excluded_from_count_and_detail(db):
    conv = await db.create_conversation("ui")
    await _turn(db, conv, 1, "good question", "good answer")
    await _turn(db, conv, 2, "poison turn", "poison answer")
    await db.mark_turn(conv, 2, "failed")  # quarantine the whole turn

    meta = await db.get_conversation_meta(conv)
    assert meta is not None
    assert meta["message_count"] == 2  # only the ok turn's two rows

    history = await db.get_history(conv, 50)
    contents = [m["content"] for m in history]
    assert "good question" in contents
    assert "poison turn" not in contents
    assert "poison answer" not in contents


async def test_get_conversation_meta_missing_returns_none(db):
    assert await db.get_conversation_meta(99999) is None


# -- endpoints -----------------------------------------------------------------


_CONFIG = {"models": {"daily": {"provider": "ollama", "model": "m"}}}


def _client(tmp_path):
    db = Database(tmp_path / "conv.db")
    conv = asyncio.run(_boot(db))
    bus = EventBus()
    gate = SafetyGate(SafetyConfig(mode="dry_run"), bus)
    agent = AgentCore(FakeProvider([]), db, conv, channel="ui", bus=bus, gate=gate)
    ctx = UIContext(db=db, bus=bus, gate=gate, agent=agent, config=_CONFIG)
    return TestClient(create_app(ctx)), db, conv


async def _boot(db: Database) -> int:
    await db.connect()
    conv = await db.create_conversation("ui")
    await _turn(db, conv, 1, "endpoint question", "endpoint answer")
    return conv


def test_conversations_endpoints(tmp_path):
    client, db, conv = _client(tmp_path)
    try:
        listing = client.get("/api/conversations").json()
        assert listing["active_conversation_id"] == conv
        ids = [c["id"] for c in listing["conversations"]]
        assert conv in ids
        one = next(c for c in listing["conversations"] if c["id"] == conv)
        assert one["title"] == "endpoint question"
        assert one["message_count"] == 2

        detail = client.get(f"/api/conversations/{conv}").json()
        assert detail["meta"]["id"] == conv
        assert [m["content"] for m in detail["messages"]] == [
            "endpoint question", "endpoint answer",
        ]

        missing = client.get("/api/conversations/424242")
        assert missing.status_code == 404
    finally:
        asyncio.run(db.close())
