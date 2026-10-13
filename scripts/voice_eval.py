"""Voice-prompt eval (DECISIONS #172): does the model act on what was MEANT?

Spoken-style transcripts -- fillers, self-corrections, a reference back to an
earlier turn, thinking aloud, Hindi and Hinglish -- go through a real model with
Baby's real voice prompt and tool schemas. Each case is scored on the tool calls,
the one fact the answer needs, and the shape of the reply (no markdown, no stock
opener, short when it should be) -- not on how it is worded. Like the bench, this
plays the tool executor with canned results, so nothing on the PC is touched.
Stricter than a live turn in one way: AgentCore re-asks once when a reply
promises an action without calling a tool, and this does not.

V8 (Hindi) only says something about a cloud model in game mode: on a normal
day the router's language pin sends a Devanagari turn to the local daily model.
To score that one, point --base-url at Ollama (http://localhost:11434/v1) and
--models at the daily model.

Usage (spends a few cents of OpenRouter credit):
    uv run python scripts/voice_eval.py --base-url https://openrouter.ai/api/v1 \\
        --key-env OPENROUTER_API_KEY --models openai/gpt-4o-mini,openai/gpt-5.4-mini
"""

from __future__ import annotations

import argparse
import asyncio
import importlib.util
import json
import os
import re
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.prompts import (  # noqa: E402
    detect_language,
    devanagari_ratio,
    hinglish_hits,
    system_prompt,
    trailing_nudge,
)
from voice.tts import EMOJI_RE  # noqa: E402

OUT_DIR = ROOT / "bench_results" / "voice_eval"
OPENED = json.dumps({"status": "opened"})
NO_FILES = json.dumps({"results": []})

_STOCK_OPENER = re.compile(
    r"^\W*(sure|of course|absolutely|certainly|great question|happy to)\b", re.IGNORECASE
)
_MARKDOWN = (
    re.compile(r"\*\*|__|`|\|"),  # bold, code, tables
    re.compile(r"^\s*#{1,6}\s", re.MULTILINE),  # headings
    # list items; one or two digits, so "180. Fifteen twelves" or a year is not one
    re.compile(r"^\s*([-*•]|\d{1,2}[.)])\s+", re.MULTILINE),
)


def words(text: str) -> int:
    return len(text.split())


def spoken_shape(text: str) -> list[str]:
    """What would sound wrong read aloud. Empty means it reads like speech."""
    problems = []
    if any(pattern.search(text) for pattern in _MARKDOWN):
        problems.append("markdown")
    if _STOCK_OPENER.search(text):
        problems.append("stock opener")
    if EMOJI_RE.search(text):
        problems.append("emoji")
    return problems


def _args(calls, name: str) -> list[dict]:
    return [args or {} for tool, args in calls if tool == name]


def score_correction_app(calls, final):
    opened = [a for a in _args(calls, "app_control") if a.get("action") == "open"]
    names = " ".join(str(a.get("name", "")).lower() for a in opened)
    return re.search(r"code|vs", names) is not None and "chrome" not in names


def score_correction_search(calls, final):
    queries = " ".join(str(a.get("query", "")).lower() for a in _args(calls, "file_search"))
    return "invoice" in queries and "budget" not in queries


def score_thinking_aloud(calls, final):
    return not calls and words(final) <= 15


def score_disfluent_question(calls, final):
    allowed = {"web_search", "fetch_page"}
    # The canned result's facts, not the product name: a good short answer need
    # not repeat the question back.
    fact = re.search(r"\b25\b|twenty[- ]five|free", final.lower()) is not None
    return fact and all(t in allowed for t, _ in calls) and words(final) <= 90


def score_reference(calls, final):
    price = r"17[, ]?999|18[, ]?000|18 ?k\b|(18|eighteen|seventeen) thousand"
    return not calls and re.search(price, final.lower()) is not None


def score_short_math(calls, final):
    said = re.search(r"\b180\b|one hundred (and )?eighty", final.lower()) is not None
    return said and words(final) <= 20


_INLINE_LIST = (
    re.compile(r"(?:^|\s)\d[.)]\s.*\s\d[.)]\s"),  # "1) this, 2) that"
    re.compile(r"\b(first|second|third) point\b", re.IGNORECASE),
)


def score_no_list(calls, final):
    return (
        not calls
        and "markdown" not in spoken_shape(final)
        and not any(pattern.search(final) for pattern in _INLINE_LIST)
    )


def score_hindi(calls, final):
    return devanagari_ratio(final) >= 0.5


def score_hinglish(calls, final):
    # The voice rule itself: Hindi words in Devanagari, English words may stay
    # Latin. A ratio threshold failed exactly that reply, because the ratio
    # skips Devanagari vowel signs.
    return devanagari_ratio(final) > 0 and hinglish_hits(final) == 0


def score_filler_then_question(calls, final):
    return "canberra" in final.lower() and words(final) <= 25


@dataclass
class VoiceCase:
    id: str
    said: str
    score: Callable
    executor: dict[str, str] = field(default_factory=dict)
    history: list[tuple[str, str]] = field(default_factory=list)


CASES = [
    VoiceCase("V1_correction_app", "uh, maybe open Chrome... no wait, actually open VS Code",
              score_correction_app, {"app_control": OPENED}),
    VoiceCase("V2_correction_search",
              "search my files for the budget sheet, no, sorry, the invoice from March",
              score_correction_search, {"file_search": NO_FILES}),
    VoiceCase("V3_thinking_aloud", "uh... hmm... let me think", score_thinking_aloud),
    VoiceCase("V4_disfluent",
              "uh what was that, wait, the database thing, Supabase, yeah, how much would "
              "that cost", score_disfluent_question,
              {"web_search": json.dumps({"results": [{"title": "Supabase pricing",
               "snippet": "Free tier; Pro plan from $25 per month."}]})}),
    VoiceCase("V5_reference", "how much was that?", score_reference,
              history=[("user", "what's a good budget phone under 20000 rupees"),
                       ("assistant", "The Redmi Note 14 at about 17,999 rupees is a solid "
                                     "pick.")]),
    VoiceCase("V6_short", "uh, what's fifteen times twelve", score_short_math),
    VoiceCase("V7_no_list", "give me three quick tips for better sleep", score_no_list),
    VoiceCase("V8_hindi", "आज शाम को क्या करूँ, कुछ बताओ", score_hindi),
    VoiceCase("V9_hinglish", "yaar kal weekend hai, kuch plan batao na", score_hinglish),
    VoiceCase("V10_filler", "umm so, like, what's the capital of Australia",
              score_filler_then_question),
]


def messages_for(case: VoiceCase) -> list[dict]:
    """What AgentCore sends for a voice turn on a fresh install: the voice prompt,
    the history, the utterance and the same trailing nudge (no memories or
    summary yet)."""
    language = detect_language(case.said)
    return [
        {"role": "system", "content": system_prompt(language=language, channel="voice")},
        *[{"role": role, "content": text} for role, text in case.history],
        {"role": "user", "content": case.said},
        {"role": "system", "content": trailing_nudge(language, "voice")},
    ]


def _bench():
    spec = importlib.util.spec_from_file_location(
        "pick_nim_model", ROOT / "scripts" / "pick_nim_model.py"
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules["pick_nim_model"] = module
    spec.loader.exec_module(module)
    return module


async def run(case: VoiceCase, provider, tools, bucket, bench) -> dict:
    outcome = bench.RunOutcome()
    messages = messages_for(case)
    calls: list = []
    final = ""
    try:
        for _ in range(4):
            text, tool_calls, _first = await bench.stream_once(
                provider, messages, tools, None, bucket, outcome
            )
            if not tool_calls:
                final = text
                break
            messages.append({"role": "assistant", "content": text or None, "tool_calls": [
                {"id": tc.id, "type": "function",
                 "function": {"name": tc.name, "arguments": tc.arguments}}
                for tc in tool_calls
            ]})
            for tc in tool_calls:
                calls.append((tc.name, bench.parse_args_json(tc.arguments)))
                canned = case.executor.get(
                    tc.name, json.dumps({"error": "tool unavailable in eval"})
                )
                messages.append({"role": "tool", "tool_call_id": tc.id, "content": canned})
    except Exception as exc:  # noqa: BLE001 — a dead run is a failed run, not a crash
        return {"pass": False, "error": f"{type(exc).__name__}: {exc}", "final": final}
    shape = spoken_shape(final)
    return {
        "pass": bool(case.score(calls, final)) and not shape,
        "meant": bool(case.score(calls, final)),
        "shape": shape,
        "calls": [[name, args] for name, args in calls],
        "final": final[:1500],
    }


async def main(models: list[str], runs: int, base_url: str, key_env: str) -> None:
    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env")
    key = os.environ.get(key_env, "")
    if not key:
        raise SystemExit(f"FAIL: {key_env} missing from .env")
    bench = _bench()
    import tools as tool_pkg
    from tools import registry

    tool_pkg.register_all()
    tools = registry.schemas()
    bucket = bench.TokenBucket(rpm=36)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    rows = []
    for model in models:
        provider = bench.NvidiaProvider(model=model, api_key=key, base_url=base_url)
        results: dict[str, list] = {}
        for case in CASES:
            for n in range(runs):
                result = await run(case, provider, tools, bucket, bench)
                results.setdefault(case.id, []).append(result)
                verdict = "PASS" if result["pass"] else "ERROR" if "error" in result else "fail"
                print(f"{model} {case.id} {n + 1}/{runs}: {verdict} "
                      f"{result.get('error') or result.get('shape') or ''}")
        # ":free" or ":nitro" in a filename is an NTFS stream on Windows.
        (OUT_DIR / (re.sub(r"[^\w.-]", "_", model) + ".json")).write_text(
            json.dumps(results, ensure_ascii=False, indent=1), encoding="utf-8")
        # A run that never reached the model says nothing about it: rate the rest,
        # and count the errors in their own column.
        answered = {cid: [r for r in rs if "error" not in r] for cid, rs in results.items()}
        rates = {cid: (sum(r["pass"] for r in rs) / len(rs) if rs else None)
                 for cid, rs in answered.items()}
        errors = sum("error" in r for rs in results.values() for r in rs)
        rows.append((model, rates, errors))
    lines = ["# Voice prompt eval", "",
             "| model | " + " | ".join(c.id for c in CASES) + " | all | errors |",
             "|---" * (len(CASES) + 3) + "|"]
    for model, rates, errors in rows:
        scored = [r for r in rates.values() if r is not None]
        overall = f"{sum(scored) / len(scored):.0%}" if scored else "-"
        lines.append(f"| {model} | "
                     + " | ".join("-" if rates[c.id] is None else f"{rates[c.id]:.0%}"
                                  for c in CASES)
                     + f" | {overall} | {errors} |")
    (OUT_DIR / "REPORT.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--models", required=True, help="comma-separated model ids")
    parser.add_argument("--runs", type=int, default=3)
    parser.add_argument("--base-url", default="https://openrouter.ai/api/v1")
    parser.add_argument("--key-env", default="OPENROUTER_API_KEY")
    args = parser.parse_args()
    asyncio.run(main([m for m in args.models.split(",") if m], args.runs,
                     args.base_url, args.key_env))
