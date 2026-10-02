"""The voice-only prompt (DECISIONS #172): what a spoken turn adds, and proof
that every other channel's prompt is byte for byte what it was."""

from __future__ import annotations

import hashlib

import pytest

from core.agent import AgentCore
from core.bus import EventBus
from core.prompts import VOICE_NUDGE, VOICE_STYLE, system_prompt, trailing_nudge
from tests.conftest import FakeProvider

# sha256 of system_prompt(...) before the voice block existed. A change here is a
# change to what typed chat, tasks and Telegram send -- on purpose or not.
_TYPED = [
    (("s", ["m"], "English", "p"),
     "13e33a7a0a34fb269eab94b0088e0c65022258bab7c7db62ce7c3c5c897c8fb8"),
    ((None, None, None, None),
     "9fa1b51749abac03e877bd3d1c925b46f8151b0deaaa0d9f016d767392592ef7"),
    (("sum", ["a", "b"], "Hindi", None),
     "ea5f80688f559ff85657337401c11ddc25d47dd62e1a7840ea255e9ffde366f8"),
]

# The trailing nudge every non-voice turn has always ended with.
_TYPED_NUDGE_END = "try the tool before saying it can't be done."


@pytest.mark.parametrize(("args", "digest"), _TYPED)
@pytest.mark.parametrize("channel", [None, "ui", "cli", "telegram", "task:7", "scheduler"])
def test_typed_prompt_is_byte_identical(args, digest, channel):
    prompt = system_prompt(*args, channel=channel)
    assert hashlib.sha256(prompt.encode()).hexdigest() == digest


@pytest.mark.parametrize("channel", [None, "ui", "cli", "telegram", "task:7", "scheduler"])
def test_typed_trailing_nudge_is_byte_identical(channel):
    """sha256 of the English nudge as core/agent.py wrote it inline before it
    moved to core/prompts.trailing_nudge."""
    nudge = trailing_nudge("English", channel)
    assert hashlib.sha256(nudge.encode()).hexdigest() == (
        "e8cc2ca861236c4d09c585bf444db8647ebe03ac19e5549c78f2d7325c7e791a"
    )


def test_voice_block_precedes_language_pin():
    prompt = system_prompt("s", ["m"], "Hindi", "p", channel="voice")
    assert VOICE_STYLE in prompt
    assert prompt.index(VOICE_STYLE) < prompt.index("## This turn")
    assert prompt.rstrip().endswith("of earlier turns or remembered facts.")  # pin stays last


def test_the_voice_block_says_what_the_spec_asks():
    """The rules the spec lists, by the words that carry them."""
    for rule in (
        "No markdown",
        "fillers",
        "act ONLY on the final version",
        "Resolve \"that\"",
        "thinking aloud",
        "No stock openers",
        "Devanagari",
    ):
        assert rule in VOICE_STYLE, rule


async def _first_request(db, channel):
    provider = FakeProvider(["ok"])
    conv = await db.create_conversation(channel)
    agent = AgentCore(provider, db, conv, channel=channel, bus=EventBus())
    await agent.run_turn("open chrome no wait open vs code")
    return provider.requests[0]


async def test_only_the_voice_agent_sends_voice_rules(db):
    voice = await _first_request(db, "voice")
    typed = await _first_request(db, "ui")
    assert VOICE_STYLE in voice[0]["content"]
    assert voice[-1]["content"].endswith(VOICE_NUDGE)
    assert VOICE_STYLE not in typed[0]["content"]
    assert typed[-1]["content"].endswith(_TYPED_NUDGE_END)
    assert VOICE_NUDGE not in typed[-1]["content"]


async def test_the_recovery_context_keeps_the_voice_rules(db):
    """When a provider rejects the history, the turn is retried from the summary;
    a spoken turn must stay a spoken turn there too."""
    conv = await db.create_conversation("voice")
    agent = AgentCore(FakeProvider([]), db, conv, channel="voice", bus=EventBus())
    recovery = agent._recovery_context(None, [], "English", "hello")
    assert VOICE_STYLE in recovery[0]["content"]
