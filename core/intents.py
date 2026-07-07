"""Multilingual yes/no + end-phrase intent parsing (EN / HI / Hinglish).

Single source of truth for short affirmative/negative answers, shared by the
safety-gate CONFIRM prompt (CLI), the proceed/cancel flow (AgentCore), and the
conversation-mode end phrases (voice). Kept import-light — pure string work, no
audio/model deps — so every surface can call it.

Safety note: conversation mode leaves the mic hot (VAD-only, no wake word) for a
minute after Baby speaks, and a "yes" there can EXECUTE a proposed action. So
`parse_yes` is deliberately strict — the whole short reply must BE an
affirmative, not merely contain one — otherwise laughter ("ha ha"), backchannel
("ok thanks"), or sarcasm ("yeah right") would approve actions. Any no-signal
vetoes a yes. Longer utterances are real turns, not votes, and match neither.
"""

from __future__ import annotations

import re

# A bare yes/no answer is short; a longer utterance is a real turn, not a vote.
_MAX_INTENT_WORDS = 4
# End phrases ("baby stop listening") run a touch longer — but stay short so a
# long utterance that merely contains one never closes the session. Configure
# end phrases at 5 words or fewer.
_MAX_END_WORDS = 5

# Exact whole-reply affirmatives (multi-word forms included). Matched only when
# the normalized utterance EQUALS one of these — never by containment.
_YES_PHRASES = (
    "y", "yes", "yeah", "yep", "yup", "yah", "ok", "okay", "okey", "sure",
    "yes please", "ok sure", "sure do", "go ahead", "do it", "please do",
    "haan", "haa", "haanji", "haan ji", "ji haan", "ji", "bilkul",
    "kar do", "kardo", "karo", "kr do", "karde", "kar de", "haan karo",
    "haan kar do", "theek hai", "thik hai", "theek", "proceed",
    # Devanagari
    "हाँ", "हां", "हा", "जी", "जी हाँ", "ठीक है", "कर दो", "करो", "ठीक", "बिल्कुल",
)
# Tokens safe to combine in a 1–2 word reply. Excludes laughter/filler-prone
# short tokens ('ha', 'ji', 'y', 'na') and English fillers ('right', 'thanks').
_YES_TOKENS = frozenset(
    "yes yeah yep yup ok okay sure haan haa kar karo kardo karde do go ahead "
    "proceed theek thik hai bilkul हाँ हां करो कर दो ठीक".split()
)

_NO_PHRASES = (
    "n", "no", "nope", "nah", "not now", "no thanks", "no thank you",
    "nahi", "nahin", "nai", "na", "mat", "mat karo", "nahi karo", "nahin karo",
    "rehne do", "rehne de", "rahne do", "chhodo", "chod do", "leave it",
    "ruk", "ruko", "skip", "cancel", "stop", "dont", "don't",
    # Devanagari
    "नहीं", "नही", "ना", "मत", "मत करो", "रहने दो", "छोड़ो", "रुको", "रुक",
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


def parse_no(text: str) -> bool:
    """True when a short utterance is a negative (no / nahi / rehne do / cancel)."""
    return _short_contains(text, _NO_PHRASES, _MAX_INTENT_WORDS)


def parse_yes(text: str) -> bool:
    """True only when a short utterance IS an affirmative (anchored, no-vetoed).

    A no-signal anywhere vetoes it ("ok cancel" / "haan nahi" → not a yes), and
    the whole reply must equal a yes phrase or be 1–2 unambiguous yes tokens.
    """
    norm = _normalize(text)
    if not norm or len(norm.split()) > _MAX_INTENT_WORDS:
        return False
    if parse_no(text):  # any negative signal wins — never proceed on ambiguity
        return False
    if norm in {_normalize(p) for p in _YES_PHRASES}:
        return True
    tokens = norm.split()
    return 1 <= len(tokens) <= 2 and all(t in _YES_TOKENS for t in tokens)


def is_end_phrase(text: str, phrases) -> bool:
    """True when a short utterance matches a configured conversation end phrase."""
    return _short_contains(text, phrases, _MAX_END_WORDS)


# -- memory commands (P4): deterministic clear / forget / wipe ----------------
# Short anchored phrases, so a real request that merely mentions "forget" ("forget
# that I said my address earlier") stays a normal turn and reaches the model.
_MAX_CMD_WORDS = 5

_NEW_CHAT = (
    "new chat", "new conversation", "start new chat", "start a new chat",
    "fresh chat", "fresh conversation", "nayi baat", "nayi baat karo",
    "naya chat", "naya conversation", "नई बात", "नयी बात",
)
_CLEAR = (
    "clear conversation", "clear this conversation", "clear the conversation",
    "clear chat", "clear this chat", "clear context",
)
_FORGET_LAST = (
    "forget that", "forget this", "forget it", "forget last",
    "bhool jao", "bhul jao", "woh bhool jao", "wo bhool jao", "bhool jaao",
    "भूल जाओ", "वो भूल जाओ",
)
# Checked BEFORE forget_last so "forget everything" / "sab kuch bhool jao" wipe,
# not just drop the last fact.
_WIPE = (
    "wipe all memory", "wipe memory", "wipe all", "wipe everything",
    "erase all memory", "erase all my memory", "delete all memory",
    "delete all my memory", "forget everything", "sab kuch mita do",
    "sab mita do", "sari memory mita do", "sab kuch bhool jao",
    "सब कुछ मिटा दो", "सब मिटा दो", "सब कुछ भूल जाओ",
)
# Confirmation must name the wipe explicitly — a bare "confirm" would fire on an
# unrelated "please confirm the booking" while the challenge is armed.
_WIPE_CONFIRM = (
    "confirm wipe", "yes wipe", "wipe confirmed", "wipe it all", "confirm the wipe",
    "haan sab mitao", "haan sab mita do", "sab mita do", "haan mita do",
    "हाँ सब मिटा दो", "सब मिटा दो",
)


def parse_memory_command(text: str) -> str | None:
    """Classify a short deterministic memory command, else None.

    Returns "wipe" | "clear" | "new_chat" | "forget_last". Wipe is matched first
    so its broader phrasing ("forget everything") is never mistaken for a
    forget-last. Wipe only ARMS a challenge; is_wipe_confirmation completes it.
    """
    if _short_contains(text, _WIPE, _MAX_CMD_WORDS):
        return "wipe"
    if _short_contains(text, _CLEAR, _MAX_CMD_WORDS):
        return "clear"
    if _short_contains(text, _NEW_CHAT, _MAX_CMD_WORDS):
        return "new_chat"
    if _short_contains(text, _FORGET_LAST, _MAX_CMD_WORDS):
        return "forget_last"
    return None


def is_wipe_confirmation(text: str) -> bool:
    """True when a short reply confirms a pending 'wipe all memory' challenge."""
    return _short_contains(text, _WIPE_CONFIRM, _MAX_CMD_WORDS)
