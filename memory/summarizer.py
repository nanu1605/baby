"""Rolling conversation summary (spec Section 12, layer 2).

Every ~10 user/assistant messages, one cheap daily-model call folds the older
turns (plus the previous summary) into conversations.summary, advancing the
summarized_upto watermark. The most recent keep_recent messages stay verbatim
in the context window; the agent loads history after the watermark.
"""

from __future__ import annotations

from core.providers.base import ChatProvider
from db.database import Database

_PROMPT = """You maintain a rolling summary of a conversation between Tanishq and his \
assistant Baby. Merge the previous summary and the new messages into ONE updated \
summary of at most 200 tokens. Keep: open tasks, decisions, user preferences and \
facts, unresolved questions. Drop: greetings, tool mechanics, resolved detours — and \
NEVER any claim that a tool, browser or capability is broken, unavailable or \
misconfigured (those are transient errors, they poison future turns; also drop such \
claims when carried in the previous summary). \
Write plain prose, no headers, same languages as used."""


class Summarizer:
    def __init__(
        self,
        provider: ChatProvider,
        db: Database,
        *,
        every: int = 10,
        keep_recent: int = 10,
    ) -> None:
        self.provider = provider
        self.db = db
        self.every = every
        self.keep_recent = keep_recent

    async def maybe_summarize(self, conversation_id: int) -> bool:
        """Fold old messages into the summary if the cadence is due."""
        summary, upto = await self.db.get_summary_state(conversation_id)
        fresh = await self.db.messages_since(conversation_id, upto)
        if len(fresh) < self.every:
            return False
        # Keep the newest keep_recent verbatim; fold everything older.
        fold = fresh[: -self.keep_recent] if self.keep_recent else fresh
        if not fold:
            return False

        transcript = "\n".join(f"{m['role']}: {m['content']}" for m in fold)
        prev = f"Previous summary:\n{summary}\n\n" if summary else ""
        messages = [
            {"role": "system", "content": _PROMPT},
            {"role": "user", "content": f"{prev}New messages:\n{transcript}"},
        ]
        parts: list[str] = []
        async for chunk in self.provider.chat(
            messages, tools=None, max_tokens=256, temperature=0.3, reasoning_effort="none"
        ):
            if chunk.delta:
                parts.append(chunk.delta)
        new_summary = "".join(parts).strip()
        if not new_summary:
            return False
        await self.db.set_summary(conversation_id, new_summary, fold[-1]["id"])
        return True
