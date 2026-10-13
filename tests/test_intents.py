"""core/intents.py — multilingual yes/no + end-phrase matrix (EN/HI/Hinglish)."""

from __future__ import annotations

import pytest

from core.intents import is_end_phrase, parse_no, parse_yes

_YES = ["yes", "Yes", "y", "yeah", "ok", "okay", "sure", "haan", "haa", "ji haan",
        "haan ji", "bilkul", "kar do", "kardo", "karo", "go ahead", "do it",
        "theek hai", "haan karo", "haan, kar do!", "  OK  ",
        "हाँ", "जी", "कर दो", "ठीक है"]
_NO = ["no", "No", "n", "nope", "nahi", "nahin", "na", "mat karo", "rehne do",
       "cancel", "skip", "stop", "don't", "nahi karo", "rehne do yaar",
       "नहीं", "मत करो", "रुको", "ok cancel"]
# Real utterances — neither a yes nor a no; caller treats as a fresh turn.
_NEITHER = ["what time is it", "open spotify and play music", "no, do the other one instead",
            "haan but only the first file please and then stop", "tell me a joke", ""]
# Casual / backchannel / sarcastic — must NEVER read as a proceed (review #1/#8).
_NOT_YES = ["ha ha", "yeah right", "ok thanks", "ok thanks bye", "oh ok",
            "han solo", "ok baby", "ok cancel", "no thanks"]


@pytest.mark.parametrize("text", _YES)
def test_parse_yes_true(text):
    assert parse_yes(text) is True


@pytest.mark.parametrize("text", _NO)
def test_parse_no_true(text):
    assert parse_no(text) is True


@pytest.mark.parametrize("text", _YES)
def test_yes_is_not_no(text):
    assert parse_no(text) is False


@pytest.mark.parametrize("text", _NO)
def test_no_is_not_yes(text):
    assert parse_yes(text) is False


@pytest.mark.parametrize("text", _NEITHER)
def test_neither_yes_nor_no(text):
    assert parse_yes(text) is False
    assert parse_no(text) is False


@pytest.mark.parametrize("text", _NOT_YES)
def test_casual_replies_never_proceed(text):
    # These must not approve an armed action in the hands-free follow-up window.
    assert parse_yes(text) is False


def test_end_phrase_matches_short_only():
    phrases = ["baby stop listening", "stop listening", "bas", "bas baby", "so jao"]
    assert is_end_phrase("Baby stop listening", phrases) is True
    assert is_end_phrase("bas", phrases) is True
    assert is_end_phrase("so jao", phrases) is True
    # A long utterance that merely contains a phrase must not close the session.
    assert is_end_phrase("bas ek aur baat batao phir", phrases) is False
    assert is_end_phrase("what is the weather", phrases) is False


# -- P4 memory commands + wipe confirmation ----------------------------------

import pytest as _pytest  # noqa: E402

from core.intents import is_wipe_confirmation, parse_memory_command  # noqa: E402


@_pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("new chat", "new_chat"),
        ("nayi baat", "new_chat"),
        ("clear conversation", "clear"),
        ("forget that", "forget_last"),
        ("woh bhool jao", "forget_last"),
        ("wipe all memory", "wipe"),
        ("sab kuch mita do", "wipe"),
        ("forget everything", "wipe"),
        ("what time is it", None),
        ("forget that I told you my home address earlier today", None),  # too long
    ],
)
def test_parse_memory_command(text, expected):
    assert parse_memory_command(text) == expected


@_pytest.mark.parametrize("text", ["confirm wipe", "haan sab mitao", "yes wipe", "सब मिटा दो"])
def test_wipe_confirmation_true(text):
    assert is_wipe_confirmation(text) is True


@_pytest.mark.parametrize(
    "text",
    ["yes", "haan", "confirm", "please confirm the booking", "confirm my appointment", "ok"],
)
def test_bare_confirm_never_wipes(text):
    # review #3: only an explicit wipe confirmation erases — never a stray yes/confirm
    assert is_wipe_confirmation(text) is False


# -- thinking aloud (DECISIONS #173) ------------------------------------------------

_FILLER = [
    "uh", "Um...", "Hmm.", "hmm, let me think", "Uh, one second.", "wait", "hold on, hold on",
    "well, so, like", "let me think about it", "matlab...", "ek second", "sochne do",
    "हम्म", "एक सेकंड", "अं... सोचने दो", "हम्म, मतलब",
]
_NOT_FILLER = [
    "uh, open chrome", "what time is it", "so what is the weather", "you know what, open notepad",
    "hmm, play some music", "matlab kya hai", "मतलब क्या है", "हम्म, बत्ती जलाओ",
    "wait, what was the price", "um um um um um um um um um",  # past the 8-word cap
    "accha", "",
]


@pytest.mark.parametrize("text", _FILLER)
def test_is_filler(text):
    from core.intents import is_filler

    assert is_filler(text)


@pytest.mark.parametrize("text", _NOT_FILLER)
def test_a_request_is_not_filler(text):
    from core.intents import is_filler

    assert not is_filler(text)


def test_filler_never_shadows_yes_or_no():
    """"ok", "haan", "ruko" can answer a pending question or confirmation, so
    nothing a yes/no parser reads may ever be swallowed as thinking aloud."""
    from core.intents import _NO_PHRASES, _YES_PHRASES, is_filler

    for phrase in (*_YES_PHRASES, *_NO_PHRASES, *_YES, *_NO):
        assert not is_filler(phrase), phrase


def test_yes_and_no_win_even_if_the_filler_list_grows(monkeypatch):
    """The lists do not overlap today; the guard keeps it that way if someone adds
    "ok" or "nahi" as a filler later."""
    import core.intents as intents

    monkeypatch.setattr(intents, "_FILLER_TOKENS", intents._FILLER_TOKENS | {"ok", "nahi"})
    assert intents.is_filler("uh")
    assert not intents.is_filler("ok")
    assert not intents.is_filler("nahi")


def test_a_question_or_assent_is_not_filler():
    from core.intents import is_filler

    for said in ("Matlab?", "मतलब?", "So?", "Hmm?", "Mm-hmm.", "Mm hm."):
        assert not is_filler(said), said
    assert is_filler("हम्म, एक सेकंड, सोचने दो")  # five spoken words, more once normalised
