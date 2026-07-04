"""Phase 4 stage 7: telegram bot handlers (no network — stub updates)."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from clients.telegram_bot import TelegramBot, chunk_text
from core.bus import EventBus
from core.safety import SafetyConfig, SafetyGate
from tests.conftest import FakeProvider

pytestmark = pytest.mark.asyncio

OWNER = 111222333


class StubMessage:
    def __init__(self, text):
        self.text = text
        self.replies: list[str] = []

    async def reply_text(self, text):
        self.replies.append(text)


class StubQuery:
    def __init__(self, data):
        self.data = data
        self.answered: list[str | None] = []
        self.edited: list[str] = []

    async def answer(self, text=None):
        self.answered.append(text)

    async def edit_message_text(self, text):
        self.edited.append(text)


def update_for(chat_id, message=None, query=None):
    return SimpleNamespace(
        effective_chat=SimpleNamespace(id=chat_id),
        effective_message=message,
        callback_query=query,
    )


async def make_bot(db, script=("telegram reply",)):
    bus = EventBus()
    gate = SafetyGate(SafetyConfig(), bus)
    bot = TelegramBot(
        token="test-token",
        chat_id=OWNER,
        db=db,
        bus=bus,
        gate=gate,
        provider=FakeProvider(list(script)),
        config={},
    )
    # Handlers under test don't need a live Application; build just the agent.
    conv = await db.create_conversation("telegram")
    from core.agent import AgentCore

    bot.agent = AgentCore(
        FakeProvider(list(script)), db, conv, channel="telegram", bus=bus, gate=gate
    )
    return bot, bus, gate


async def test_foreign_chat_ignored_and_logged(db):
    bot, bus, _ = await make_bot(db)
    q = bus.subscribe()
    message = StubMessage("hello baby")
    await bot._on_message(update_for(999, message=message), None)
    assert message.replies == []
    texts = []
    while not q.empty():
        texts.append(q.get_nowait().payload.get("text", ""))
    assert any("foreign chat" in t for t in texts)


async def test_owner_message_gets_reply(db):
    bot, _, _ = await make_bot(db, script=("namaste from baby",))
    message = StubMessage("kaisa hai baby?")
    await bot._on_message(update_for(OWNER, message=message), None)
    assert message.replies == ["namaste from baby"]


async def test_long_reply_chunked(db):
    bot, _, _ = await make_bot(db, script=("x" * 9000,))
    message = StubMessage("write a lot")
    await bot._on_message(update_for(OWNER, message=message), None)
    assert len(message.replies) == 3
    assert "".join(message.replies) == "x" * 9000


async def test_busy_lock_replies_without_second_turn(db):
    bot, _, _ = await make_bot(db)
    message = StubMessage("second message")
    async with bot._lock:
        await bot._on_message(update_for(OWNER, message=message), None)
    assert message.replies == ["Still working on your last message — one moment."]


async def test_callback_resolves_pending_confirmation(db):
    bot, _, gate = await make_bot(db)

    async def confirm():
        return await gate.confirmations.ask(
            tool="run_shell", command="del x", explanation="deletes x", channel="telegram"
        )

    pending = asyncio.create_task(confirm())
    await asyncio.sleep(0.01)  # let ask() register + publish
    confirm_id = next(iter(gate.confirmations._pending))
    query = StubQuery(f"confirm:{confirm_id}:1")
    await bot._on_callback(update_for(OWNER, query=query), None)
    ok, resolution = await pending
    assert ok is True and resolution == "approved"
    assert query.answered == ["Done"]
    assert "Approved" in query.edited[0]


async def test_callback_from_foreign_chat_ignored(db):
    bot, _, gate = await make_bot(db)

    async def confirm():
        return await gate.confirmations.ask(
            tool="run_shell", command="del x", explanation="", channel="telegram"
        )

    pending = asyncio.create_task(confirm())
    await asyncio.sleep(0.01)
    confirm_id = next(iter(gate.confirmations._pending))
    query = StubQuery(f"confirm:{confirm_id}:1")
    await bot._on_callback(update_for(999, query=query), None)
    assert query.answered == []  # untouched — still pending
    gate.confirmations.resolve(confirm_id, False)
    await pending


async def test_confirm_watcher_sends_keyboard_for_telegram_channel_only(db):
    bot, bus, _ = await make_bot(db)
    sent: list[dict] = []

    async def send_message(**kwargs):
        sent.append(kwargs)

    bot._app = SimpleNamespace(bot=SimpleNamespace(send_message=send_message))
    watcher = asyncio.create_task(bot._confirm_watcher())
    await asyncio.sleep(0.01)
    bus.publish("confirm_request", "ui", confirm_id="a", command="x", explanation="")
    bus.publish(
        "confirm_request", "telegram", confirm_id="b", command="del y", explanation="removes y"
    )
    await asyncio.sleep(0.05)
    watcher.cancel()
    assert len(sent) == 1
    assert "del y" in sent[0]["text"]
    assert sent[0]["reply_markup"] is not None


async def test_start_without_token_returns_false(db):
    bus = EventBus()
    bot = TelegramBot(
        token="",
        chat_id=0,
        db=db,
        bus=bus,
        gate=SafetyGate(SafetyConfig(), bus),
        provider=FakeProvider([]),
        config={},
    )
    assert await bot.start() is False


@pytest.mark.asyncio(loop_scope="function")
async def test_chunk_text_boundaries():
    assert chunk_text("") == [""]
    assert chunk_text("abc", size=2) == ["ab", "c"]
