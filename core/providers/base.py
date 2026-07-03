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
