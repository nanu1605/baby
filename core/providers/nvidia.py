"""NVIDIA NIM provider via integrate.api.nvidia.com's OpenAI-compatible API.

Cloud-primary brain for the NIM migration (NIM_MIGRATION_PLAN.md): same
AsyncOpenAI wire as Ollama/Gemini, auth via NVIDIA_API_KEY (nvapi- prefix).
A 429 or connection failure puts the provider into a short cooldown that the
router reads; the Phase N2 health state machine layers its own probing on top
of `probe()` — `healthy()` itself stays cheap (no network) because the router
consults it on every pick.

reasoning_effort is deliberately NOT forwarded yet: NIM models differ in
unknown-parameter tolerance, and internal capped calls route local anyway.
The N1 bench measures per-model tolerance before any forwarding is added.
"""

from __future__ import annotations

import time
from collections.abc import AsyncIterator

import httpx
from openai import APIConnectionError, APIStatusError, AsyncOpenAI

from core.providers.base import Chunk, accumulate_stream

NIM_OPENAI_URL = "https://integrate.api.nvidia.com/v1"
COOLDOWN_429_S = 90.0


class NvidiaProvider:
    """Drives a NIM catalog model through the OpenAI-compat chat-completions API."""

    name = "nvidia"

    def __init__(
        self,
        model: str,
        api_key: str,
        temperature: float = 0.7,
        base_url: str = NIM_OPENAI_URL,
        cooldown_s: float = COOLDOWN_429_S,
    ) -> None:
        self.model = model
        self.api_key = api_key
        self.temperature = temperature
        self.base_url = base_url
        self.cooldown_s = cooldown_s
        self.unhealthy_until = 0.0  # monotonic deadline of the current cooldown
        # AsyncOpenAI refuses an empty api_key; healthy() already reports a
        # missing key, so a placeholder keeps construction fail-soft.
        self._client = AsyncOpenAI(base_url=base_url, api_key=api_key or "missing")

    def _mark_unhealthy(self) -> None:
        self.unhealthy_until = time.monotonic() + self.cooldown_s

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
        """Key present and not cooling down — cheap, no network (router hot path)."""
        return bool(self.api_key) and time.monotonic() >= self.unhealthy_until

    async def probe(self, generation: bool = False) -> bool:
        """Active connectivity probe for the health state machine (Phase N2).

        Default: GET /v1/models — proves DNS + TLS + auth without burning
        generation quota. generation=True adds a 1-token completion (used only
        for the DEGRADED→CLOUD recovery transition, which must prove that
        generation actually works).
        """
        try:
            async with httpx.AsyncClient(timeout=5) as client:
                resp = await client.get(
                    f"{self.base_url.rstrip('/')}/models",
                    headers={"Authorization": f"Bearer {self.api_key}"},
                )
            if resp.status_code != 200:
                return False
        except httpx.HTTPError:
            return False
        if not generation:
            return True
        if not self.model:
            return False
        try:
            await self._client.chat.completions.create(
                model=self.model,
                messages=[{"role": "user", "content": "ping"}],
                max_tokens=1,
                stream=False,
            )
            return True
        except (APIStatusError, APIConnectionError):
            return False
