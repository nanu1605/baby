"""Long-term memory tools: remember / recall / forget.

All three are ALLOW: they mutate only Baby's own memory rows, and the spec
wants "remember X" / "forget that" to be frictionless (Section 12).
"""

from __future__ import annotations

import json

from tools.registry import tool

_store = None  # MemoryStore, injected at boot via configure()


def configure(store) -> None:
    global _store
    _store = store


def _unavailable() -> str:
    return json.dumps({"error": "memory is not available"})


@tool
async def remember(fact: str) -> str:
    """Store a durable fact about the user for future sessions."""
    if _store is None:
        return _unavailable()
    return json.dumps(await _store.add_fact(fact, source="explicit"), ensure_ascii=False)


@tool
async def recall(query: str, k: int = 5) -> str:
    """Search stored facts about the user by meaning."""
    if _store is None:
        return _unavailable()
    return json.dumps({"facts": await _store.search(query, k)}, ensure_ascii=False)


@tool
async def forget(query: str) -> str:
    """Deactivate the stored fact closest to the query."""
    if _store is None:
        return _unavailable()
    return json.dumps(await _store.forget(query), ensure_ascii=False)
