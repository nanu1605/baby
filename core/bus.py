"""Async event bus: the single emission path for everything Baby does.

Every surface (CLI, web UI, later voice/telegram) subscribes to the same
stream. Durable audit rows are written inline at the dispatch site, NOT
through the bus — bus delivery is best-effort (drop-oldest under
backpressure) so a slow subscriber can never stall the agent.
"""

from __future__ import annotations

import asyncio
import itertools
from dataclasses import dataclass, field
from datetime import UTC, datetime

# Event kinds (the contract consumed by UI/CLI/tests):
#   turn_start      {conversation_id}
#   token           {text}
#   turn_end        {reply, status: ok|cancelled|capped|error}
#   tool_start      {call_id, tool, args, safety_class}
#   tool_end        {call_id, tool, safety_class, status, result_summary}
#   confirm_request {confirm_id, tool, command, explanation, timeout_s}
#   confirm_resolved{confirm_id, approved, resolution}
#   status          {text}
#   error           {text}


@dataclass
class InboundMessage:
    """A user message arriving from any channel."""

    channel: str  # cli | ui | voice | telegram | scheduler
    text: str
    conversation_id: int | None = None
    lang: str | None = None


@dataclass
class AgentEvent:
    """One observable step of Baby's work."""

    kind: str
    channel: str
    payload: dict = field(default_factory=dict)
    seq: int = 0
    ts: str = ""


class EventBus:
    """Fan-out pub/sub over per-subscriber asyncio queues.

    publish() is synchronous and non-blocking; single loop, no locks.
    """

    def __init__(self, maxsize: int = 512) -> None:
        self._maxsize = maxsize
        self._subscribers: list[asyncio.Queue[AgentEvent]] = []
        self._seq = itertools.count(1)

    def subscribe(self) -> asyncio.Queue[AgentEvent]:
        q: asyncio.Queue[AgentEvent] = asyncio.Queue(self._maxsize)
        self._subscribers.append(q)
        return q

    def unsubscribe(self, q: asyncio.Queue[AgentEvent]) -> None:
        if q in self._subscribers:
            self._subscribers.remove(q)

    def publish(self, kind: str, channel: str, **payload) -> AgentEvent:
        event = AgentEvent(
            kind=kind,
            channel=channel,
            payload=payload,
            seq=next(self._seq),
            ts=datetime.now(UTC).isoformat(timespec="milliseconds"),
        )
        for q in self._subscribers:
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                # Drop the oldest event rather than block the agent.
                try:
                    q.get_nowait()
                except asyncio.QueueEmpty:
                    pass
                try:
                    q.put_nowait(event)
                except asyncio.QueueFull:
                    pass
        return event
