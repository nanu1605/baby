"""Phase 4 stage 3: Notifier fan-out (toast/voice/telegram, each best-effort)."""

from __future__ import annotations

import pytest

import workers.notify as notify_mod
from core.bus import EventBus
from workers.notify import Notifier

pytestmark = pytest.mark.asyncio


@pytest.fixture
def toasts(monkeypatch):
    calls: list[tuple[str, str]] = []
    monkeypatch.setattr(notify_mod, "_toast_blocking", lambda t, m: calls.append((t, m)))
    return calls


class FakeVoice:
    def __init__(self, boom=False):
        self.spoken: list[str] = []
        self.boom = boom

    def announce(self, text):
        if self.boom:
            raise RuntimeError("audio died")
        self.spoken.append(text)


async def test_announce_hits_all_tiers(toasts):
    notifier = Notifier({}, EventBus())
    voice = FakeVoice()
    sent: list[str] = []

    async def telegram(text):
        sent.append(text)

    notifier.voice = voice
    notifier.telegram_send = telegram
    await notifier.announce("briefing ready")
    assert toasts and toasts[0][1] == "briefing ready"
    assert voice.spoken == ["briefing ready"]
    assert sent == ["briefing ready"]


async def test_task_finished_formats_and_respects_notify_flag(toasts):
    notifier = Notifier({}, EventBus())
    await notifier.task_finished(title="ev research", ok=True, result_line="3 EVs found", notify=0)
    assert toasts == []
    await notifier.task_finished(title="ev research", ok=True, result_line="3 EVs found", notify=1)
    assert "ev research" in toasts[0][1] and "is done" in toasts[0][1]
    await notifier.task_finished(title="bad", ok=False, result_line="boom", notify=1)
    assert "failed" in toasts[1][1]


async def test_tiers_fail_independently(toasts):
    notifier = Notifier({}, EventBus())
    notifier.voice = FakeVoice(boom=True)
    sent: list[str] = []

    async def telegram(text):
        sent.append(text)

    notifier.telegram_send = telegram
    await notifier.announce("still delivered")  # voice raises; toast+telegram land
    assert toasts and sent == ["still delivered"]


async def test_missing_tiers_do_not_raise(monkeypatch):
    def broken_toast(t, m):
        raise OSError("no toast support")

    monkeypatch.setattr(notify_mod, "_toast_blocking", broken_toast)
    notifier = Notifier({}, EventBus())
    await notifier.announce("nothing available")  # no voice, no telegram, toast broken
