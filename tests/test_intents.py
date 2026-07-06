"""core/intents.py — multilingual yes/no + end-phrase matrix (EN/HI/Hinglish)."""

from __future__ import annotations

import pytest

from core.intents import is_end_phrase, parse_no, parse_yes

_YES = ["yes", "Yes", "y", "yeah", "ok", "okay", "sure", "haan", "haa", "ji haan",
        "kar do", "kardo", "karo", "go ahead", "do it", "theek hai", "haan karo",
        "haan, kar do!", "  OK  "]
_NO = ["no", "No", "n", "nope", "nahi", "nahin", "na", "mat karo", "rehne do",
       "cancel", "skip", "stop", "don't", "nahi karo", "rehne do yaar"]
# Real utterances — neither a yes nor a no; caller treats as a fresh turn.
_NEITHER = ["what time is it", "open spotify and play music", "no, do the other one instead",
            "haan but only the first file please and then stop", "tell me a joke", ""]


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


def test_end_phrase_matches_short_only():
    phrases = ["baby stop listening", "stop listening", "bas", "bas baby", "so jao"]
    assert is_end_phrase("Baby stop listening", phrases) is True
    assert is_end_phrase("bas", phrases) is True
    assert is_end_phrase("so jao", phrases) is True
    # A long utterance that merely contains a phrase must not close the session.
    assert is_end_phrase("bas ek aur baat batao phir", phrases) is False
    assert is_end_phrase("what is the weather", phrases) is False
