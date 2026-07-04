"""ChatProvider protocol — every brain (Ollama, Gemini) speaks this interface."""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable


@dataclass
class ToolCall:
    """One tool invocation requested by the model."""

    id: str
    name: str
    arguments: str  # raw JSON string, parsed by the agent loop


@dataclass
class Chunk:
    """One streamed piece of a model response.

    delta carries incremental text; tool_calls is populated only on the
    final chunk of a turn that requests tools; done marks end of response.
    """

    delta: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    done: bool = False


@runtime_checkable
class ChatProvider(Protocol):
    """Minimal contract for a chat model backend."""

    name: str

    def chat(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
        **opts,
    ) -> AsyncIterator[Chunk]: ...

    async def healthy(self) -> bool: ...


async def accumulate_stream(stream) -> AsyncIterator[Chunk]:
    """OpenAI-format completion stream → Chunks.

    Streaming tool calls arrive fragmented across events; reassemble them by
    index. Shared by every OpenAI-wire provider (Ollama, Gemini).
    """
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
