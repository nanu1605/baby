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


def sanitize_history(messages: list[dict]) -> list[dict]:
    """Flatten foreign tool exchanges to plain text for Gemini.

    Gemini 3 rejects replayed functionCall parts that lack a
    thought_signature — i.e. every tool call born on another model — with
    HTTP 400 ("Function call is missing a thought_signature", observed
    live), which is exactly the shape a mid-tool-loop router fallback
    sends. Textualizing the exchange keeps the backstop useful: the model
    still sees what was called and what came back, just not as native
    tool-call parts.
    """
    out: list[dict] = []
    for message in messages:
        if message.get("role") == "assistant" and message.get("tool_calls"):
            lines = [
                f"[called {tc['function']['name']}"
                f"({tc['function'].get('arguments') or '{}'})]"
                for tc in message["tool_calls"]
            ]
            text = message.get("content") or ""
            out.append(
                {"role": "assistant",
                 "content": (text + "\n" if text else "") + "\n".join(lines)}
            )
        elif message.get("role") == "tool":
            out.append(
                {"role": "user",
                 "content": f"[tool result] {message.get('content') or ''}"}
            )
        else:
            out.append(message)
    return out


class GeminiProvider:
    """Drives a Gemini model through the OpenAI-compat chat-completions API."""

    name = "gemini"

    def __init__(
        self,
        model: str,
        api_key: str,
        temperature: float = 0.7,
        base_url: str = GEMINI_OPENAI_URL,
        emit_usage: bool = True,
    ) -> None:
        self.model = model
        self.api_key = api_key
        self.temperature = temperature
        self.emit_usage = emit_usage  # stream_options.include_usage (P5 telemetry)
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
        create_kwargs: dict = dict(
            model=self.model,
            messages=sanitize_history(messages),
            tools=tools or None,
            temperature=opts.get("temperature", self.temperature),
            max_tokens=opts.get("max_tokens"),
            stream=True,
        )
        if self.emit_usage:
            create_kwargs["stream_options"] = {"include_usage": True}
        try:
            stream = await self._client.chat.completions.create(**create_kwargs)
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
