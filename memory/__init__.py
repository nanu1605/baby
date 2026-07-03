"""Memory package: long-term facts, rolling summary, fact extraction.

build_memory() is the one wiring point shared by CLI and UI. It fails soft:
if the embedder or vector store can't come up, Baby runs with Phase-1
behavior (no memory) instead of refusing to start.
"""

from __future__ import annotations

from dataclasses import dataclass

from core.providers.base import ChatProvider
from db.database import Database
from memory.embedder import Embedder
from memory.extractor import FactExtractor
from memory.store import MemoryStore
from memory.summarizer import Summarizer


@dataclass
class Memory:
    store: MemoryStore
    summarizer: Summarizer
    extractor: FactExtractor


async def build_memory(config: dict, db: Database, provider: ChatProvider) -> Memory | None:
    """Construct + init the memory stack; None (with a note) on failure."""
    from tools import memory_tools

    mem_cfg = config.get("memory", {})
    embedder = Embedder(config.get("models", {}).get("embedder", "intfloat/multilingual-e5-small"))
    store = MemoryStore(
        db,
        embedder,
        k=mem_cfg.get("k", 5),
        min_similarity=mem_cfg.get("min_similarity", 0.80),
        dedup_similarity=mem_cfg.get("dedup_similarity", 0.90),
    )
    try:
        await store.init()
        await embedder.warmup()
    except Exception as exc:  # noqa: BLE001 — memory must fail soft
        print(f"memory unavailable — continuing without it ({exc})")
        return None
    memory_tools.configure(store)
    return Memory(
        store=store,
        summarizer=Summarizer(
            provider,
            db,
            every=mem_cfg.get("summarize_every", 10),
            keep_recent=mem_cfg.get("keep_recent", 10),
        ),
        extractor=FactExtractor(provider, db, store, every=mem_cfg.get("extract_every", 20)),
    )
