"""Automatic fact extraction (spec Section 12, layer 3, "extracted" source).

Every ~20 user/assistant messages, ask the model for durable user-specific
facts as a JSON list; the store deduplicates by embedding similarity before
insert. Failures are silent — extraction is best-effort maintenance and must
never disturb a turn.
"""

from __future__ import annotations

import json

from core.providers.base import ChatProvider
from db.database import Database
from memory.store import MemoryStore

_PROMPT = """From this conversation, extract durable facts about the user (Tanishq) \
worth remembering across sessions: preferences, people, projects, possessions, \
routines, recurring tasks. Only facts stated or clearly implied by the USER — \
nothing speculative, nothing about this conversation's mechanics, no transient \
state. Reply with ONLY a JSON array of short self-contained strings, [] if none. \
Keep each fact in the language the user used."""

_MAX_FACT_LEN = 300


def parse_facts(raw: str) -> list[str]:
    """Model output → list of fact strings; anything unparseable → []."""
    text = raw.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        text = "\n".join(line for line in lines if not line.startswith("```"))
    start, end = text.find("["), text.rfind("]")
    if start == -1 or end <= start:
        return []
    try:
        data = json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return []
    if not isinstance(data, list):
        return []
    return [item.strip()[:_MAX_FACT_LEN] for item in data if isinstance(item, str) and item.strip()]


class FactExtractor:
    def __init__(
        self,
        provider: ChatProvider,
        db: Database,
        store: MemoryStore,
        *,
        every: int = 20,
    ) -> None:
        self.provider = provider
        self.db = db
        self.store = store
        self.every = every

    async def maybe_extract(self, conversation_id: int) -> int:
        """Extract + store new facts if the cadence is due. Returns inserts."""
        upto = await self.db.get_extracted_upto(conversation_id)
        fresh = await self.db.messages_since(conversation_id, upto)
        if len(fresh) < self.every:
            return 0

        transcript = "\n".join(f"{m['role']}: {m['content']}" for m in fresh)
        messages = [
            {"role": "system", "content": _PROMPT},
            {"role": "user", "content": transcript},
        ]
        parts: list[str] = []
        async for chunk in self.provider.chat(
            messages, tools=None, max_tokens=512, temperature=0.2, reasoning_effort="none"
        ):
            if chunk.delta:
                parts.append(chunk.delta)
        facts = parse_facts("".join(parts))

        inserted = 0
        for fact in facts:
            result = await self.store.add_fact(fact, source="extracted")
            if result.get("stored"):
                inserted += 1
        # Watermark advances even when parsing yields nothing — one shot per span.
        await self.db.set_extracted_upto(conversation_id, fresh[-1]["id"])
        return inserted
