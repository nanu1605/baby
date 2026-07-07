"""ChatProvider protocol — every brain (Ollama, Gemini) speaks this interface."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

# How long to wait for the include_usage trailer AFTER generation finishes. The
# reply is already complete at finish_reason; this cap keeps a slow or dropped
# trailer from delaying (or failing) the terminating chunk — the router treats a
# long post-content stall as a mid-reply abort, so this MUST stay well under its
# stall timeout. Token usage is best-effort: miss it rather than risk the turn.
_TRAILER_TIMEOUT_S = 2.0


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
    usage rides the final (done) chunk when the host reports token counts
    (stream_options.include_usage) — None when the host omits them.
    """

    delta: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    done: bool = False
    usage: dict | None = None


def _usage_dict(usage) -> dict | None:
    """OpenAI/Ollama CompletionUsage → the three counts we persist (P5).

    Null-safe: any host that omits usage leaves it None so telemetry degrades
    to "no tokens shown", never a crash. Ollama maps its native eval counts to
    the same OpenAI-wire fields, so no special-casing is needed here.
    """
    if usage is None:
        return None
    prompt = getattr(usage, "prompt_tokens", 0) or 0
    completion = getattr(usage, "completion_tokens", 0) or 0
    total = getattr(usage, "total_tokens", 0) or (prompt + completion)
    return {
        "prompt_tokens": int(prompt),
        "completion_tokens": int(completion),
        "total_tokens": int(total),
    }


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
    usage: dict | None = None
    async for event in stream:
        # Some hosts put usage on a content event; capture it if so (read before
        # the empty-choices skip so the include_usage trailer isn't missed).
        if getattr(event, "usage", None):
            usage = _usage_dict(event.usage)
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
            # The reply is complete. With include_usage the token counts ride a
            # trailing event AFTER finish_reason — grab it best-effort, bounded,
            # and swallow any stall/drop so the terminating chunk (which carries
            # tool_calls) is ALWAYS delivered and the turn never fails on a slow
            # or missing trailer.
            if usage is None:
                for _ in range(2):  # trailer is the next 0-1 events; cap the wait
                    try:
                        trailer = await asyncio.wait_for(
                            stream.__anext__(), timeout=_TRAILER_TIMEOUT_S
                        )
                    except Exception:  # noqa: BLE001 — usage is best-effort, never fatal
                        break
                    if getattr(trailer, "usage", None):
                        usage = _usage_dict(trailer.usage)
                        break
            yield Chunk(tool_calls=calls, done=True, usage=usage)
            return
    yield Chunk(done=True, usage=usage)
