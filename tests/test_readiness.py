"""Readiness: the logon race — Ollama may come up after Baby (spec Section 13)."""

from __future__ import annotations

import core.readiness as readiness
from core.providers.base import Chunk


class ScriptedProvider:
    """healthy() pops a scripted list; chat warms instantly."""

    name = "scripted"
    num_ctx = 8192

    def __init__(self, healthy_script: list[bool]):
        self.healthy_script = list(healthy_script)
        self.healthy_calls = 0

    async def healthy(self) -> bool:
        self.healthy_calls += 1
        if self.healthy_script:
            return self.healthy_script.pop(0)
        return True

    async def chat(self, messages, tools=None, **opts):
        yield Chunk(delta="pong", done=True)

    async def loaded_context_length(self):
        return None


async def test_healthy_provider_passes_without_waiting(db):
    provider = ScriptedProvider([True])
    ok, notes = await readiness.ready_check(provider, db, wait_s=120)
    assert ok is True
    assert not any("waiting" in n for n in notes)


async def test_unreachable_with_zero_wait_fails_fast(db, monkeypatch):
    monkeypatch.setattr(readiness, "_try_start_ollama", lambda notes: None)
    provider = ScriptedProvider([False])
    ok, notes = await readiness.ready_check(provider, db, wait_s=0)
    assert ok is False
    assert any("not reachable" in n for n in notes)
    assert provider.healthy_calls == 1  # no polling loop


async def test_waits_until_ollama_comes_up(db, monkeypatch):
    monkeypatch.setattr(readiness, "_try_start_ollama", lambda notes: None)

    async def instant_sleep(_s):
        return None

    monkeypatch.setattr(readiness.asyncio, "sleep", instant_sleep)
    provider = ScriptedProvider([False, False, True])
    ok, notes = await readiness.ready_check(provider, db, wait_s=60)
    assert ok is True
    assert any("waiting up to 60s" in n for n in notes)
    assert any(n.startswith("Ollama up after") for n in notes)
    assert provider.healthy_calls == 3
