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
    rag_k: int = 0  # P4: cross-session past-message snippets per turn (0 = engine v1)


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
    # Cross-session RAG (P4) rides the memory.engine flag: v2 injects past-
    # message snippets and embeds new messages; v1 leaves both off (rollback).
    engine_v2 = str(mem_cfg.get("engine", "v1")) == "v2"
    rag_k = int(mem_cfg.get("rag_k", 4)) if engine_v2 else 0
    return Memory(
        store=store,
        summarizer=Summarizer(
            provider,
            db,
            every=mem_cfg.get("summarize_every", 10),
            keep_recent=mem_cfg.get("keep_recent", 10),
        ),
        extractor=FactExtractor(provider, db, store, every=mem_cfg.get("extract_every", 20)),
        rag_k=rag_k,
    )
