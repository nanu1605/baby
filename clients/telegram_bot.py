"""Telegram client: Baby in your pocket, locked to one chat id (spec §16).

Embedded in the existing asyncio loop — python-telegram-bot's run_polling()
owns the loop, so we drive the documented manual sequence instead:
initialize → start → updater.start_polling, and the reverse on stop.

Security: every update from a chat other than TELEGRAM_CHAT_ID is silently
ignored (and logged). Confirmations surface as inline Yes/No buttons whose
callbacks resolve the same ConfirmationManager the web modal uses — the
gate itself is untouched.
"""

from __future__ import annotations

import asyncio
import contextlib

from core.agent import AgentCore

_CHUNK = 4096  # Telegram message size limit


def chunk_text(text: str, size: int = _CHUNK) -> list[str]:
    return [text[i : i + size] for i in range(0, len(text), size)] or [""]


class TelegramBot:
    def __init__(
        self,
        *,
        token: str,
        chat_id: int,
        db,
        bus,
        gate,
        provider,
        config: dict,
        memory=None,
    ) -> None:
        self.token = token
        self.chat_id = int(chat_id)
        self.db = db
        self.bus = bus
        self.gate = gate
        self.provider = provider
        self.config = config
        self.memory = memory
        self.agent: AgentCore | None = None
        self._app = None
        self._watcher: asyncio.Task | None = None
        self._lock = asyncio.Lock()

    # -- lifecycle ------------------------------------------------------------

    async def start(self) -> bool:
        """True when polling is live; False (logged) on any setup failure."""
        if not self.token or not self.chat_id:
            self.bus.publish(
                "status", "telegram", text="telegram: disabled (missing token or chat id)"
            )
            return False
        try:
            from telegram.ext import (
                Application,
                CallbackQueryHandler,
                MessageHandler,
                filters,
            )

            conv = await self.db.latest_conversation("telegram")
            if conv is None:
                conv = await self.db.create_conversation("telegram")
            self.agent = AgentCore(
                self.provider,
                self.db,
                conv,
                channel="telegram",
                bus=self.bus,
                gate=self.gate,
                memory=self.memory,
                suggest_next_step=self.config.get("persona", {}).get("suggest_next_step", True),
            )
            self._app = Application.builder().token(self.token).build()
            self._app.add_handler(
                MessageHandler(filters.TEXT & ~filters.COMMAND, self._on_message)
            )
            self._app.add_handler(CallbackQueryHandler(self._on_callback))
            await self._app.initialize()
            await self._app.start()
            await self._app.updater.start_polling()
            self._watcher = asyncio.create_task(self._confirm_watcher())
            self.bus.publish("status", "telegram", text="telegram: connected (owner chat only)")
            return True
        except Exception as exc:  # noqa: BLE001 — telegram failure must not block boot
            self.bus.publish(
                "status", "telegram", text=f"telegram: failed to start — {type(exc).__name__}: {exc}"
            )
            self._app = None
            return False

    async def stop(self) -> None:
        if self._watcher is not None:
            self._watcher.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._watcher
        if self._app is not None:
            with contextlib.suppress(Exception):
                await self._app.updater.stop()
                await self._app.stop()
                await self._app.shutdown()
            self._app = None

    async def send_to_owner(self, text: str) -> None:
        if self._app is None:
            return
        for piece in chunk_text(text):
            await self._app.bot.send_message(chat_id=self.chat_id, text=piece)

    # -- handlers ---------------------------------------------------------------

    def _is_owner(self, update) -> bool:
        chat = getattr(update, "effective_chat", None)
        return chat is not None and chat.id == self.chat_id

    async def _on_message(self, update, context) -> None:
        message = update.effective_message
        if not self._is_owner(update):
            # Spec §18: answer ONLY the owner chat; everything else is logged.
            self.bus.publish(
                "status",
                "telegram",
                text=f"telegram: ignored message from foreign chat "
                f"{getattr(update.effective_chat, 'id', '?')}",
            )
            return
        text = (message.text or "").strip()
        if not text:
            return
        if self._lock.locked():
            await message.reply_text("Still working on your last message — one moment.")
            return
        async with self._lock:
            reply = await self.agent.run_turn(text)
        for piece in chunk_text(reply):
            await message.reply_text(piece)

    async def _on_callback(self, update, context) -> None:
        query = update.callback_query
        if not self._is_owner(update):
            return
        data = str(getattr(query, "data", "") or "")
        if not data.startswith("confirm:"):
            await query.answer()
            return
        _, confirm_id, flag = data.split(":", 2)
        approved = flag == "1"
        resolved = self.gate.confirmations.resolve(confirm_id, approved)
        await query.answer("Done" if resolved else "Expired")
        with contextlib.suppress(Exception):
            await query.edit_message_text(
                f"{'✅ Approved' if approved else '❌ Denied'}"
                if resolved
                else "⏱️ Expired — already resolved or timed out"
            )

    async def _confirm_watcher(self) -> None:
        """Bus confirm_requests on the telegram channel → inline Yes/No."""
        from telegram import InlineKeyboardButton, InlineKeyboardMarkup

        q = self.bus.subscribe()
        try:
            while True:
                event = await q.get()
                if event.kind != "confirm_request" or event.channel != "telegram":
                    continue
                cid = event.payload.get("confirm_id", "")
                keyboard = InlineKeyboardMarkup(
                    [
                        [
                            InlineKeyboardButton("✅ Yes", callback_data=f"confirm:{cid}:1"),
                            InlineKeyboardButton("❌ No", callback_data=f"confirm:{cid}:0"),
                        ]
                    ]
                )
                text = (
                    f"Baby wants to run:\n{event.payload.get('command', '?')}\n\n"
                    f"{event.payload.get('explanation', '')}\n"
                    f"(auto-NO in {int(event.payload.get('timeout_s', 60))}s)"
                )
                with contextlib.suppress(Exception):
                    await self._app.bot.send_message(
                        chat_id=self.chat_id, text=text, reply_markup=keyboard
                    )
        finally:
            self.bus.unsubscribe(q)
