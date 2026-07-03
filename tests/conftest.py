"""Shared fixtures: FakeProvider + in-memory-style temp database."""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest_asyncio

from core.providers.base import Chunk, ToolCall
from db.database import Database


class FakeProvider:
    """Scripted ChatProvider: yields queued responses, records every request.

    Script entries are either a plain string (final text answer) or a list
    of ToolCall (a tool-calling turn).
    """

    name = "fake"

    def __init__(self, script: list[str | list[ToolCall]]) -> None:
        self.script = list(script)
        self.requests: list[list[dict]] = []

    async def chat(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
        **opts,
    ) -> AsyncIterator[Chunk]:
        self.requests.append([dict(m) for m in messages])
        if not self.script:
            yield Chunk(delta="(script exhausted)", done=False)
            yield Chunk(done=True)
            return
        step = self.script.pop(0)
        if isinstance(step, str):
            yield Chunk(delta=step)
            yield Chunk(done=True)
        else:
            yield Chunk(tool_calls=step, done=True)

    async def healthy(self) -> bool:
        return True


@pytest_asyncio.fixture
async def db(tmp_path):
    database = Database(tmp_path / "test.db")
    await database.connect()
    yield database
    await database.close()
