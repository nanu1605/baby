"""scripts/voice_eval.py scorers and message shape, offline (DECISIONS #172)."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

from core.prompts import VOICE_NUDGE, VOICE_STYLE

_SPEC = importlib.util.spec_from_file_location(
    "voice_eval", Path(__file__).parent.parent / "scripts" / "voice_eval.py"
)
voice_eval = importlib.util.module_from_spec(_SPEC)
sys.modules["voice_eval"] = voice_eval
_SPEC.loader.exec_module(voice_eval)


def test_spoken_shape_flags_what_sounds_wrong_read_aloud():
    shape = voice_eval.spoken_shape
    assert shape("It's ten past six.") == []
    assert shape("Sure! It's ten past six.") == ["stock opener"]
    assert shape("**Ten** past six") == ["markdown"]
    assert shape("Tips:\n- sleep early\n- no screens") == ["markdown"]
    assert shape("1. Sleep early") == ["markdown"]
    assert shape("Great, see you 👍") == ["emoji"]
    assert shape("It's 2.5 km away, about 30 minutes.") == []  # a decimal is not a list


def test_the_correction_scorers_reward_only_the_final_version():
    app = voice_eval.score_correction_app
    assert app([("app_control", {"action": "open", "name": "VS Code"})], "Opening it.")
    assert not app([("app_control", {"action": "open", "name": "chrome"}),
                    ("app_control", {"action": "open", "name": "code"})], "")
    assert not app([], "Opening VS Code.")  # said it, did not do it
    search = voice_eval.score_correction_search
    assert search([("file_search", {"query": "invoice march"})], "")
    assert not search([("file_search", {"query": "budget"}),
                       ("file_search", {"query": "invoice"})], "")


def test_the_other_scorers():
    assert voice_eval.score_thinking_aloud([], "Take your time.")
    assert not voice_eval.score_thinking_aloud([("web_search", {})], "")
    assert voice_eval.score_reference([], "That one was about 17,999 rupees.")
    assert not voice_eval.score_reference([], "Which one do you mean?")
    assert voice_eval.score_short_math([], "That's 180.")
    assert voice_eval.score_hindi([], "आज शाम पार्क में टहलने जाइए।")
    assert not voice_eval.score_hinglish([], "kal movie dekhne chalo")
    assert voice_eval.score_filler_then_question([], "Canberra.")


async def test_a_case_is_sent_the_way_the_voice_agent_sends_it(db):
    """Byte for byte what AgentCore(channel="voice") sends on a fresh install."""
    from core.agent import AgentCore
    from core.bus import EventBus
    from tests.conftest import FakeProvider

    for case in voice_eval.CASES:
        provider = FakeProvider(["ok"])
        conv = await db.create_conversation("voice")
        for role, text in case.history:
            await db.add_message(conv, role, text)
        agent = AgentCore(provider, db, conv, channel="voice", bus=EventBus())
        await agent.run_turn(case.said)
        assert voice_eval.messages_for(case) == provider.requests[0], case.id
    assert VOICE_STYLE in voice_eval.messages_for(case)[0]["content"]
    assert voice_eval.messages_for(case)[-1]["content"].endswith(VOICE_NUDGE)


def test_scorers_accept_good_spoken_answers_and_refuse_bad_ones():
    ev = voice_eval
    searched = [("web_search", {})]
    assert ev.score_disfluent_question(searched, "It's free to start; Pro is 25 dollars.")
    assert not ev.score_disfluent_question([], "I'm not sure, sorry.")
    assert ev.score_reference([], "About 18 thousand rupees.")
    assert ev.score_reference([], "Around eighteen thousand rupees.")
    assert not ev.score_short_math([], "That's 1800.")
    assert ev.score_short_math([], "180. Fifteen twelves.")
    assert ev.spoken_shape("2019. It launched that March.") == []
    assert not ev.score_no_list([], "Here you go: 1) fixed bedtime, 2) no screens, 3) cool room.")
    assert not ev.score_no_list([], "First point: fixed bedtime. Second point: no screens.")
    assert ev.score_no_list([], "Keep a fixed bedtime, skip screens late, and keep the room cool.")
    # The voice rule exactly: Hindi words in Devanagari, English words in Latin.
    # Under 0.3 Devanagari by ratio, which skips vowel signs.
    mixed = "Weekend पर friends के साथ short trip plan करो, या घर पर movie marathon रखो।"
    assert ev.score_hinglish([], mixed)
    assert not ev.score_hinglish([], "Weekend pe friends ke saath trip plan karo yaar.")


async def test_a_run_that_never_reached_the_model_is_an_error_not_a_fail():
    # A stand-in for scripts/pick_nim_model.py, which loads .env when imported.
    class Bench:
        RunOutcome = object

        @staticmethod
        async def stream_once(*a):
            raise RuntimeError("call failed after 5 attempts")

    result = await voice_eval.run(voice_eval.CASES[0], None, [], None, Bench)
    assert result["pass"] is False and "call failed" in result["error"]
