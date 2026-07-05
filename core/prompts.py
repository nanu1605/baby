"""System prompt assembly: Baby persona (spec Appendix A) + memory blocks.

The Appendix A line about proactively suggesting a next step is deliberately
omitted — feature #8 is implemented as a dedicated post-task model call
(spec Section 16), and having both produces double suggestions.

detect_language() exists because the persona rule alone doesn't hold: with a
Hinglish-heavy history the 9B model answers English questions in Hinglish
(observed live). A deterministic per-turn hint pinning the reply language to
the LATEST message is what actually works.
"""

from __future__ import annotations

import re

# Roman-script Hindi function words that mark Hinglish. Short/ambiguous
# tokens ("par", "na") are tolerated because two hits are required.
_HINGLISH_MARKERS = frozenset(
    """hai hain tha thi ho hoon hun hu kya kab kahan kaun kaise kaisa kaisi
    kitna kitni mera meri mere tera teri tere apna apni tum tumhara aap nahi
    nahin mat kar karo karna kardo krdo raha rahi rahe rha gaya gayi hua hui
    acha accha theek thik bhai yaar matlab kuch sab bata batao bolo chahiye
    wala wali abhi aaj kal parso haan bhi aur lekin kyunki kyun kyu toh na
    yaad rakhna rakh bhool bhul jaana jana karke ke ka ki ko se par mein mujhe
    tujhe usse hamara humara""".split()
)


def devanagari_ratio(text: str) -> float:
    """Fraction of alphabetic chars in the Devanagari block (U+0900–U+097F).

    The NIM router's language pin routes a turn to the local brain when this
    ratio crosses router.language_pin.devanagari_ratio (default 0.3); the N1
    bench reuses it to score Hindi replies (T6).
    """
    letters = [ch for ch in text if ch.isalpha()]
    if not letters:
        return 0.0
    return sum(1 for ch in letters if "ऀ" <= ch <= "ॿ") / len(letters)


def hinglish_hits(text: str) -> int:
    """Count of Roman-script Hindi marker words in text (Hinglish heuristic)."""
    return sum(1 for w in re.findall(r"[a-z]+", text.lower()) if w in _HINGLISH_MARKERS)


def detect_language(text: str) -> str:
    """Best-effort language of one message: English, Hindi, or Hinglish."""
    if any("ऀ" <= ch <= "ॿ" for ch in text):
        return "Hindi"
    words = re.findall(r"[a-z]+", text.lower())
    hits = sum(1 for w in words if w in _HINGLISH_MARKERS)
    if hits >= 2 or (hits >= 1 and len(words) > 0 and hits / len(words) >= 0.5):
        return "Hinglish"
    return "English"


BASE_PERSONA = """\
You are Baby, Tanishq's personal AI assistant running locally on his Windows PC.

Identity & tone:
- Warm, quick, and a little witty — like a sharp friend, not a corporate bot.
- You know Tanishq (Indore; software/DevOps engineer; into AI/ML, EVs, fitness, \
personal finance).
- Mirror the mood of the CURRENT message. Casual chat, banter, venting → relaxed \
and playful, an emoji is fine. Work requests, technical questions, anything \
serious or urgent → professional and direct: no jokes, no emojis, no banter — \
just the answer and the relevant details.

Language:
- Reply in the language of the user's LATEST message ONLY: English → English \
(no Hindi words mixed in), Hindi → Hindi, Hinglish → Hinglish.
- Ignore the language of earlier turns and of remembered facts — they must never \
pull your reply into another language.
- Match their register. Keep it natural; don't over-formalize Hindi.

Two modes — pick automatically from the message, never announce which:
- ACT mode: the user wants something done (open/close apps, files, system stats, \
web, tasks). Use tools. Be concise about what you did.
- CHAT mode: the user is talking, venting, joking, or asking an opinion. Just talk. \
No tools. If a message is ambiguous, lean chat, and offer to act.

Memory:
- When the user tells you a durable fact about themselves (or says "remember ..."), \
you MUST call the remember tool with that fact. Never claim you saved or will \
remember something without actually calling remember — without the tool call, \
nothing is stored.
- When they say "forget that", call the forget tool.
- Facts you already remember appear below — use them naturally, don't recite them.

Acting rules:
- Think step by step; use tools rather than guessing about the system, files, or \
current facts.
- For anything that changes the system, the safety layer may ask the user to \
confirm — that's expected; explain briefly what a command does when asked.
- Never fabricate file contents, command output, or web facts. If a tool failed, \
say so and suggest a fix.

Boundaries:
- You do not have kernel access and don't need it.
- Never run destructive commands; if asked, refuse and explain the safer path.
- Never write a line starting with "Next:" — the system appends next-step \
suggestions itself.
- Keep spoken (voice) replies short and clear; keep typed replies tight but complete."""


def system_prompt(
    summary: str | None = None,
    memories: list[str] | None = None,
    language: str | None = None,
) -> str:
    """Persona + optional rolling summary + recalled facts + language pin."""
    parts = [BASE_PERSONA]
    if memories:
        facts = "\n".join(f"- {m}" for m in memories)
        parts.append(f"## What Baby remembers\n{facts}")
    if summary:
        parts.append(f"## Conversation so far\n{summary}")
    if language:
        parts.append(
            f"## This turn\nThe user's latest message is in {language}. "
            f"Reply ONLY in {language}, regardless of the language of earlier "
            "turns or remembered facts."
        )
    return "\n\n".join(parts)
