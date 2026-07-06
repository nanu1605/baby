"""P0 repro (#6): empty model output must never surface "(no response)".

Today core/agent.py:239 falls back to the literal "(no response)" when both
the main no-tools call AND the forced _final_answer retry come back empty
(observed live — E2E T09 returned it three runs straight). P1's loop guard
keeps the single retry but replaces the placeholder with an honest failure
line, so silence never reaches the user.

These tests fail against current code (reply IS "(no response)") and go green
once P1 lands.
"""

from __future__ import annotations

from core.agent import AgentCore
from tests.conftest import FakeProvider


async def _agent(db, script):
    provider = FakeProvider(script)
    conv_id = await db.create_conversation("cli")
    return AgentCore(provider, db, conv_id, channel="cli"), provider


async def test_empty_output_never_yields_no_response(db):
    # Main no-tools call empty; the forced _final_answer retry is empty too.
    agent, _ = await _agent(db, ["", ""])
    reply = await agent.run_turn("what's up?")
    assert reply.strip(), "empty reply reached the user"
    assert "(no response)" not in reply


async def test_empty_output_retries_once_then_honest_line(db):
    agent, provider = await _agent(db, ["", ""])
    reply = await agent.run_turn("hello?")
    # One retry happened: main call + the _final_answer nudge = 2 provider calls.
    assert len(provider.requests) >= 2
    # An honest, human failure line — not the placeholder, not empty.
    assert "(no response)" not in reply
    assert any(ch.isalpha() for ch in reply), "failure line should read like a sentence"
