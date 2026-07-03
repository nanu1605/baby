"""e5 embeddings on CPU.

The e5 family requires "query: " on searches and "passage: " on stored text;
getting this wrong silently halves retrieval quality (spec Section 12), so the
prefixes live here and nowhere else. _embed() is the single seam tests override.
"""

from __future__ import annotations

import asyncio

QUERY_PREFIX = "query: "
PASSAGE_PREFIX = "passage: "

DIMENSIONS = 384  # intfloat/multilingual-e5-small


class Embedder:
    """Lazy-loaded sentence-transformers model, CPU-only, one encode at a time."""

    def __init__(self, model_name: str = "intfloat/multilingual-e5-small") -> None:
        self.model_name = model_name
        self._model = None
        self._lock = asyncio.Lock()

    async def warmup(self) -> None:
        """Load the model (single-flight). Raises if the model can't load."""
        async with self._lock:
            if self._model is None:
                self._model = await asyncio.to_thread(self._load)

    def _load(self):
        from sentence_transformers import SentenceTransformer  # heavy; lazy

        return SentenceTransformer(self.model_name, device="cpu")

    async def embed_query(self, text: str) -> list[float]:
        return await self._embed(QUERY_PREFIX + text)

    async def embed_passage(self, text: str) -> list[float]:
        return await self._embed(PASSAGE_PREFIX + text)

    async def _embed(self, text: str) -> list[float]:
        await self.warmup()
        async with self._lock:  # ST encode isn't guaranteed re-entrant
            vector = await asyncio.to_thread(self._model.encode, text, normalize_embeddings=True)
        return [float(x) for x in vector]
