"""Task-completion and briefing notifications: toast + voice + telegram.

Called DIRECTLY by the worker pool and scheduler — never via the bus, which
is drop-oldest best-effort by design; a completion notice must not be
droppable. Every delivery tier fails independently and silently: a broken
toast must not eat the telegram push.
"""

from __future__ import annotations

import asyncio


def _toast_blocking(title: str, message: str) -> None:
    from winotify import Notification

    Notification(app_id="Baby", title=title, msg=message[:200]).show()


class Notifier:
    """Fan-out for owner-facing announcements (feature #10)."""

    def __init__(self, config: dict, bus) -> None:
        self.config = config
        self.bus = bus
        # Injected by run_ui after the respective subsystems come up:
        self.voice = None  # VoicePipeline (announce(text)) or None
        self.telegram_send = None  # async callable(text) or None

    async def announce(self, text: str, *, toast_title: str = "Baby") -> None:
        """Toast + spoken + telegram, each best-effort."""
        try:
            await asyncio.to_thread(_toast_blocking, toast_title, text)
        except Exception:  # noqa: BLE001 — a failed toast never blocks the rest
            pass
        if self.voice is not None:
            try:
                self.voice.announce(text)
            except Exception:  # noqa: BLE001
                pass
        if self.telegram_send is not None:
            try:
                await self.telegram_send(text)
            except Exception:  # noqa: BLE001
                pass

    async def task_finished(
        self, *, title: str, ok: bool, result_line: str, notify: int = 1
    ) -> None:
        if not notify:
            return
        verdict = "is done" if ok else "failed"
        text = f"Baby: your task '{title}' {verdict}. {result_line}".strip()
        await self.announce(text, toast_title="Baby — task " + ("done" if ok else "failed"))
