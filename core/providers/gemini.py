"""Gemini provider via Google's OpenAI-compatible endpoint.

Free-tier cloud fallback (DECISIONS.md): reuses the same openai AsyncClient
wire as Ollama — no google SDK. Rate limits (429) and server errors (5xx) put
the provider into a cooldown that the router reads; cloud is never a hard
dependency.
"""

from __future__ import annotations

import time
from collections.abc import AsyncIterator

from openai import APIConnectionError, APIStatusError, AsyncOpenAI

from core.providers.base import Chunk, accumulate_stream

GEMINI_OPENAI_URL = "https://generativelanguage.googleapis.com/v1beta/openai/"
COOLDOWN_S = 300.0


class GeminiProvider:
    """Drives a Gemini model through the OpenAI-compat chat-completions API."""

    name = "gemini"

    def __init__(
        self,
        model: str,
        api_key: str,
        temperature: float = 0.7,
        base_url: str = GEMINI_OPENAI_URL,
    ) -> None:
        self.model = model
        self.api_key = api_key
        self.temperature = temperature
        self.unhealthy_until = 0.0  # monotonic deadline of the current cooldown
        self._client = AsyncOpenAI(base_url=base_url, api_key=api_key)

    def _mark_unhealthy(self) -> None:
        self.unhealthy_until = time.monotonic() + COOLDOWN_S

    async def chat(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
        **opts,
    ) -> AsyncIterator[Chunk]:
        try:
            stream = await self._client.chat.completions.create(
                model=self.model,
                messages=messages,
                tools=tools or None,
                temperature=opts.get("temperature", self.temperature),
                max_tokens=opts.get("max_tokens"),
                stream=True,
            )
            async for chunk in accumulate_stream(stream):
                yield chunk
        except APIStatusError as exc:
            if exc.status_code == 429 or exc.status_code >= 500:
                self._mark_unhealthy()
            raise
        except APIConnectionError:
            self._mark_unhealthy()
            raise

    async def healthy(self) -> bool:
        """Key present and not cooling down after a 429/5xx."""
        return bool(self.api_key) and time.monotonic() >= self.unhealthy_until
