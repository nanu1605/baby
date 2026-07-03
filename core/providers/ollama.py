"""Ollama provider via its OpenAI-compatible endpoint."""

from __future__ import annotations

from collections.abc import AsyncIterator

import httpx
from openai import AsyncOpenAI

from core.providers.base import Chunk, ToolCall


class OllamaProvider:
    """Drives a local Ollama model through the /v1 chat-completions API."""

    name = "ollama"

    def __init__(
        self,
        model: str,
        base_url: str = "http://127.0.0.1:11434/v1",
        temperature: float = 0.7,
        keep_alive: str = "24h",
        num_ctx: int = 8192,
    ) -> None:
        self.model = model
        self.base_url = base_url
        self.temperature = temperature
        self.keep_alive = keep_alive
        self.num_ctx = num_ctx
        self._client = AsyncOpenAI(base_url=base_url, api_key="ollama")

    async def chat(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
        **opts,
    ) -> AsyncIterator[Chunk]:
        # Ollama honors keep_alive on its OpenAI endpoint via extra body.
        # num_ctx is belt-and-braces: OLLAMA_CONTEXT_LENGTH is the reliable
        # mechanism (see DECISIONS.md), but pass it in case the endpoint
        # honors options.
        extra_body: dict = {
            "keep_alive": self.keep_alive,
            "options": {"num_ctx": self.num_ctx},
        }
        # Thinking models (qwen3.5) burn max_tokens in the reasoning channel
        # and return empty content when capped. reasoning_effort="none" is the
        # only /v1 knob that disables thinking (verified; think/false and
        # chat_template_kwargs are ignored) — internal calls with tight caps
        # (summary, extraction, next-step) must pass it.
        if opts.get("reasoning_effort"):
            extra_body["reasoning_effort"] = opts["reasoning_effort"]
        stream = await self._client.chat.completions.create(
            model=self.model,
            messages=messages,
            tools=tools or None,
            temperature=opts.get("temperature", self.temperature),
            max_tokens=opts.get("max_tokens"),
            stream=True,
            extra_body=extra_body,
        )
        # Streaming tool calls arrive fragmented; accumulate by index.
        pending: dict[int, dict] = {}
        async for event in stream:
            if not event.choices:
                continue
            choice = event.choices[0]
            delta = choice.delta
            if delta.content:
                yield Chunk(delta=delta.content)
            for tc in delta.tool_calls or []:
                slot = pending.setdefault(tc.index, {"id": "", "name": "", "args": ""})
                if tc.id:
                    slot["id"] = tc.id
                if tc.function and tc.function.name:
                    slot["name"] = tc.function.name
                if tc.function and tc.function.arguments:
                    slot["args"] += tc.function.arguments
            if choice.finish_reason:
                calls = [
                    ToolCall(id=s["id"] or f"call_{i}", name=s["name"], arguments=s["args"])
                    for i, s in sorted(pending.items())
                ]
                yield Chunk(tool_calls=calls, done=True)
                return
        yield Chunk(done=True)

    async def loaded_context_length(self) -> int | None:
        """Context size Ollama actually loaded the model with (None if unknown).

        The /v1 endpoint ignores options.num_ctx (verified empirically), so the
        served context depends on OLLAMA_CONTEXT_LENGTH — this lets callers
        detect silent truncation instead of trusting config.
        """
        root = self.base_url.rsplit("/v1", 1)[0]
        try:
            async with httpx.AsyncClient(timeout=5) as client:
                data = (await client.get(f"{root}/api/ps")).json()
        except (httpx.HTTPError, ValueError):
            return None
        for m in data.get("models", []):
            if m.get("name") == self.model:
                return m.get("context_length")
        return None

    async def healthy(self) -> bool:
        root = self.base_url.rsplit("/v1", 1)[0]
        try:
            async with httpx.AsyncClient(timeout=3) as client:
                resp = await client.get(f"{root}/api/tags")
                return resp.status_code == 200
        except httpx.HTTPError:
            return False
