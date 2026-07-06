"""Multilingual yes/no + end-phrase intent parsing (EN / HI / Hinglish).

Single source of truth for short affirmative/negative answers, shared by the
safety-gate CONFIRM prompt (CLI), the proceed/cancel flow (AgentCore), and the
conversation-mode end phrases (voice). Kept import-light — pure string work, no
audio/model deps — so every surface can call it.

Only SHORT transcripts match: a real instruction ("no, do the other one
instead") must never be read as a bare "no". Anything that is neither a clear
yes nor a clear no returns False from both, so callers treat it as a fresh turn.
"""

from __future__ import annotations

import re

# A bare yes/no answer is short; a longer utterance is a real turn, not a vote.
_MAX_INTENT_WORDS = 4
# End phrases ("baby stop listening") run a touch longer.
_MAX_END_WORDS = 5

_YES_PHRASES = (
    "y", "yes", "yeah", "yep", "yup", "yah", "ok", "okay", "okey", "sure",
    "haan", "haa", "ha", "han", "ji", "ji haan", "haan ji",
    "kar do", "kardo", "karo", "kr do", "karde", "kar de",
    "go ahead", "proceed", "do it", "please do", "sure do",
    "theek hai", "thik hai", "haan karo", "haan kar do", "bilkul",
)
_NO_PHRASES = (
    "n", "no", "nope", "nah", "not now", "no thanks",
    "nahi", "nahin", "nai", "na", "mat", "mat karo", "nahi karo",
    "rehne do", "rehne de", "rahne do", "chhodo", "chhodo", "chod do",
    "ruk", "ruko", "skip", "cancel", "stop", "dont", "don't", "leave it",
)


def _normalize(text: str) -> str:
    """Lowercase, strip punctuation (keeps Devanagari via \\w), collapse space."""
    return " ".join(re.sub(r"[^\w]+", " ", text.lower()).split())


def _short_contains(text: str, phrases, max_words: int) -> bool:
    norm = _normalize(text)
    if not norm or len(norm.split()) > max_words:
        return False
    padded = f" {norm} "
    return any(f" {_normalize(p)} " in padded for p in phrases)


# A negation token flips an otherwise-affirmative verb: "karo" is yes, but
# "nahi karo" / "mat karo" is a refusal — never read those as a proceed.
_NEGATORS = ("no", "not", "nahi", "nahin", "nai", "na", "mat", "dont", "don't", "never")


def parse_yes(text: str) -> bool:
    """True when a short utterance is an affirmative (yes / haan / kar do / ok)."""
    if _short_contains(text, _NEGATORS, _MAX_INTENT_WORDS):
        return False
    return _short_contains(text, _YES_PHRASES, _MAX_INTENT_WORDS)


def parse_no(text: str) -> bool:
    """True when a short utterance is a negative (no / nahi / rehne do / cancel)."""
    return _short_contains(text, _NO_PHRASES, _MAX_INTENT_WORDS)


def is_end_phrase(text: str, phrases) -> bool:
    """True when a short utterance matches a configured conversation end phrase."""
    return _short_contains(text, phrases, _MAX_END_WORDS)
