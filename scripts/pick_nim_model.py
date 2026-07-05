"""Phase N1 shootout: pick nim_primary + nim_heavy empirically (spec §4 N1).

Runs Baby's REAL tool schemas (tools/registry.py — nothing hand-copied)
against each candidate NIM model with a fixed T1–T9 battery, playing the
tool-executor role with canned results. The script recommends; Tanishq
decides (winners go into config.yaml, rationale into DECISIONS.md).

Usage:
    uv run python scripts/pick_nim_model.py                 # full default shortlist
    uv run python scripts/pick_nim_model.py --models a,b    # explicit candidates
    uv run python scripts/pick_nim_model.py --runs 5        # N per test (default 5)

Good citizen: shared 36 RPM token bucket, exponential backoff on 429, and
per-model result caching in bench_results/*.json — interrupted runs resume
instead of re-burning free-tier quota. Run at Tanishq's real usage hours
(evening IST) to capture peak congestion.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import statistics
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env")

from openai import APIConnectionError, APIError, APIStatusError, AsyncOpenAI  # noqa: E402

from core.prompts import devanagari_ratio, hinglish_hits  # noqa: E402
from core.providers.nvidia import NIM_OPENAI_URL, NvidiaProvider  # noqa: E402
from core.ratelimit import TokenBucket  # noqa: E402
from workers.orchestrator import _PLAN_PROMPT, parse_plan  # noqa: E402

RESULTS_DIR = ROOT / "bench_results"
TRANSCRIPT_DIR = RESULTS_DIR / "transcripts"

# Exact catalog IDs verified live against GET /v1/models on 2026-07-05
# (121 models served; spec's zhipuai/glm-5.2 is actually z-ai/glm-5.2,
# Kimi K2.5 is gone — only k2.6 is served).
DEFAULT_MODELS = [
    "nvidia/nemotron-3-super-120b-a12b",
    "mistralai/mistral-nemotron",
    "moonshotai/kimi-k2.6",
    "z-ai/glm-5.2",
    "minimaxai/minimax-m2.7",
    "meta/llama-4-maverick-17b-128e-instruct",
    "qwen/qwen3.5-122b-a10b",
]

# Heavy-slot candidates additionally run T9 (planning decomposition).
DEFAULT_HEAVY = {
    "nvidia/nemotron-3-super-120b-a12b",
    "moonshotai/kimi-k2.6",
    "z-ai/glm-5.2",
    "minimaxai/minimax-m2.7",
    "qwen/qwen3.5-122b-a10b",
}

SYSTEM = (
    "You are Baby, Tanishq's personal AI assistant on his Windows 11 PC. "
    "Use the available tools when a request needs an action or live "
    "information; reply directly when it doesn't. Match the user's language "
    "(English, Hindi, or Hinglish)."
)

MAX_RETRIES = 4  # per model call, exponential backoff on 429/transient


# -- canned tool results ---------------------------------------------------------

STATS_RESULT = json.dumps(
    {"cpu_percent": 34.2, "ram_percent": 61.0, "gpu": {"vram_used_gb": 6.5, "vram_total_gb": 8.0}}
)
SEARCH_RESULT = json.dumps(
    {"results": [{"path": "C:\\Users\\tanis\\Documents\\quarterly_report.pdf", "size_kb": 812}]}
)
SEARCH_BACKUP_RESULT = json.dumps(
    {
        "results": [
            {"path": "C:\\Users\\tanis\\scripts\\backup.py", "size_kb": 4},
            {"path": "C:\\Users\\tanis\\old\\backup_v2.py", "size_kb": 6},
        ]
    }
)
READ_RESULT = json.dumps(
    {"path": "C:\\Users\\tanis\\Documents\\quarterly_report.pdf",
     "text": "Quarterly Report Q2. Revenue: $4.2M. Costs: $3.1M. Margin improved 6%."}
)
CLOSE_RESULT = json.dumps({"status": "closed", "app": "chrome"})
SPOTIFY_ERROR = json.dumps({"error": "app not found: spotify is not installed on this PC"})


# -- scoring helpers (pure — unit-tested in tests/test_bench.py) -----------------


def parse_args_json(raw: str) -> dict | None:
    """Tool-call arguments string → dict, None when unparseable."""
    try:
        data = json.loads(raw) if raw.strip() else {}
    except (ValueError, TypeError):
        return None
    return data if isinstance(data, dict) else None


def extract_json_object(text: str) -> dict | None:
    """First {...} block in text → dict (T8), None when absent/invalid."""
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        return None
    try:
        data = json.loads(text[start : end + 1])
    except (ValueError, TypeError):
        return None
    return data if isinstance(data, dict) else None


def honest_about_failure(text: str) -> bool:
    """T4: did the reply acknowledge the tool error instead of claiming success?"""
    lowered = text.lower()
    admits = any(
        marker in lowered
        for marker in (
            "not installed", "couldn't", "could not", "can't", "cannot",
            "unable", "fail", "error", "not found", "nahi", "नहीं",
        )
    )
    claims_open = bool(re.search(r"\bopen(?:ed|ing)?\b", lowered)) and "spotify" in lowered
    return admits or not claims_open


def score_t1(calls: list[tuple[str, dict | None]], final: str) -> dict:
    names = [n for n, _ in calls]
    close_args = next((a for n, a in calls if n == "app_control" and a), None)
    args_ok = bool(
        close_args
        and str(close_args.get("action", "")).lower() == "close"
        and "chrom" in str(close_args.get("name", "")).lower()
    )
    return {
        "pass": "app_control" in names and "get_system_stats" in names and args_ok,
        "correct_tools": "app_control" in names and "get_system_stats" in names,
        "args_ok": args_ok,
        "mentions_cpu": "34" in final,
    }


def score_t2(calls: list[tuple[str, dict | None]], final: str) -> dict:
    names = [n for n, _ in calls]
    ordered = (
        "file_search" in names
        and "read_file" in names
        and names.index("file_search") < names.index("read_file")
    )
    read_args = next((a for n, a in calls if n == "read_file" and a), None)
    exact_path = bool(
        read_args
        and read_args.get("path") == "C:\\Users\\tanis\\Documents\\quarterly_report.pdf"
    )
    return {
        "pass": ordered and exact_path and "4.2" in final,
        "chain_ordered": ordered,
        "args_ok": exact_path,
        "answer_ok": "4.2" in final,
    }


def score_t3(calls: list[tuple[str, dict | None]], final: str) -> dict:
    search_args = next((a for n, a in calls if n == "file_search" and a), None)
    query_ok = bool(search_args and "backup" in str(search_args.get("query", "")).lower())
    limit_ok = bool(search_args and search_args.get("max_results") == 5)
    return {"pass": query_ok and limit_ok, "args_ok": query_ok and limit_ok,
            "query_ok": query_ok, "limit_ok": limit_ok}


def score_t4(calls: list[tuple[str, dict | None]], final: str) -> dict:
    honest = honest_about_failure(final)
    return {"pass": honest, "honest": honest,
            "retried": sum(1 for n, _ in calls if n == "app_control") >= 2}


def score_t5(calls: list[tuple[str, dict | None]], final: str) -> dict:
    return {"pass": not calls and bool(final.strip()), "zero_tools": not calls}


def score_t6(calls: list[tuple[str, dict | None]], final: str) -> dict:
    ratio = devanagari_ratio(final)
    return {"pass": ratio >= 0.3, "devanagari_ratio": round(ratio, 3)}


def score_t7(calls: list[tuple[str, dict | None]], final: str) -> dict:
    hits = hinglish_hits(final)
    return {"pass": hits >= 2, "hinglish_hits": hits}


def score_t8(calls: list[tuple[str, dict | None]], final: str) -> dict:
    data = extract_json_object(final)
    ok = bool(data and "city" in data and "why" in data)
    return {"pass": ok, "json_valid": data is not None, "keys_ok": ok}


def score_t9(calls: list[tuple[str, dict | None]], final: str) -> dict:
    plan = parse_plan(final, 4)
    ok = bool(plan and 2 <= len(plan) <= 4)
    return {"pass": ok, "plan_valid": plan is not None,
            "subtasks": len(plan) if plan else 0}


@dataclass
class Case:
    id: str
    prompt: str
    executor: dict[str, str]
    score: object
    heavy_only: bool = False
    max_tokens: int | None = None
    system: str = SYSTEM
    max_steps: int = 5
    use_tools: bool = True  # T9 mirrors the orchestrator plan call: tools=None


CASES = [
    Case("T1", "Close Chrome and tell me the CPU usage.",
         {"app_control": CLOSE_RESULT, "get_system_stats": STATS_RESULT}, score_t1),
    Case("T2", "Find the file quarterly_report.pdf on my PC, read it, and tell me the "
               "revenue number from it.",
         {"file_search": SEARCH_RESULT, "read_file": READ_RESULT}, score_t2),
    Case("T3", "Search my files for python scripts named backup, show at most 5 results.",
         {"file_search": SEARCH_BACKUP_RESULT}, score_t3),
    Case("T4", "Open Spotify.", {"app_control": SPOTIFY_ERROR}, score_t4),
    Case("T5", "aur baby, kya chal raha hai?", {}, score_t5),
    Case("T6", "आज का दिन बहुत अच्छा रहा। मुझे हिंदी में कल के लिए एक छोटी सी "
               "प्रेरणादायक बात लिखकर दो।", {}, score_t6),
    Case("T7", "yaar baby, weekend pe ghar pe hi chill karna hai, kuch mast plan "
               "batao na", {}, score_t7),
    Case("T8", 'Reply with ONLY a JSON object, no prose and no code fences: '
               '{"city": "...", "why": "..."} — the best Indian city for street food.',
         {}, score_t8),
    Case("T9", "Build me a personal finance tracker web app: expense entry form, "
               "monthly charts, CSV export, and a README.",
         {}, score_t9, heavy_only=True, max_tokens=1500,
         system=_PLAN_PROMPT.format(n=4), use_tools=False),
]


# -- runner ----------------------------------------------------------------------


@dataclass
class RunOutcome:
    ok: bool = False
    score: dict = field(default_factory=dict)
    calls: list = field(default_factory=list)  # [(name, raw_args)]
    valid_arg_calls: int = 0
    final: str = ""
    first_token_s: float | None = None
    total_s: float = 0.0
    steps: int = 0
    count_429: int = 0
    stream_errors: int = 0
    error: str = ""

    def to_json(self) -> dict:
        return {
            "ok": self.ok, "score": self.score, "calls": self.calls,
            "valid_arg_calls": self.valid_arg_calls, "final": self.final[:2000],
            "first_token_s": self.first_token_s, "total_s": round(self.total_s, 3),
            "steps": self.steps, "count_429": self.count_429,
            "stream_errors": self.stream_errors, "error": self.error,
        }


async def stream_once(provider, messages, tools, max_tokens, bucket, outcome):
    """One model call with bucket + backoff; returns (text, tool_calls, first_s)."""
    for attempt in range(MAX_RETRIES + 1):
        await bucket.acquire_wait()
        t0 = time.monotonic()
        text, calls, first_s = "", [], None
        try:
            async for chunk in provider.chat(messages, tools=tools, max_tokens=max_tokens):
                if first_s is None and (chunk.delta or chunk.tool_calls):
                    first_s = time.monotonic() - t0
                text += chunk.delta
                calls.extend(chunk.tool_calls)
            return text, calls, first_s
        except APIStatusError as exc:
            if exc.status_code == 429:
                outcome.count_429 += 1
            else:
                outcome.stream_errors += 1
        except (APIConnectionError, APIError):
            # Includes mid-stream server pushbacks like "ResourceExhausted:
            # Worker local total request limit reached" (seen live in N0).
            outcome.stream_errors += 1
        if attempt < MAX_RETRIES:
            await asyncio.sleep(min(60.0, 2.0 ** (attempt + 1)))
    raise RuntimeError(f"call failed after {MAX_RETRIES + 1} attempts")


async def run_case(provider, case: Case, tools, bucket) -> RunOutcome:
    outcome = RunOutcome()
    messages = [
        {"role": "system", "content": case.system},
        {"role": "user", "content": case.prompt},
    ]
    started = time.monotonic()
    try:
        case_tools = tools if case.use_tools else None
        for _ in range(case.max_steps):
            outcome.steps += 1
            text, tool_calls, first_s = await stream_once(
                provider, messages, case_tools, case.max_tokens, bucket, outcome
            )
            if outcome.first_token_s is None:
                outcome.first_token_s = first_s
            if not tool_calls:
                outcome.final = text
                break
            messages.append(
                {
                    "role": "assistant",
                    "content": text or None,
                    "tool_calls": [
                        {"id": tc.id, "type": "function",
                         "function": {"name": tc.name, "arguments": tc.arguments}}
                        for tc in tool_calls
                    ],
                }
            )
            for tc in tool_calls:
                outcome.calls.append([tc.name, tc.arguments])
                if parse_args_json(tc.arguments) is not None:
                    outcome.valid_arg_calls += 1
                result = case.executor.get(
                    tc.name, json.dumps({"error": "tool unavailable in bench"})
                )
                messages.append(
                    {"role": "tool", "tool_call_id": tc.id, "content": result}
                )
        parsed = [(n, parse_args_json(a)) for n, a in outcome.calls]
        outcome.score = case.score(parsed, outcome.final)
        outcome.ok = True
    except Exception as exc:  # noqa: BLE001 — a dead run is data, not a crash
        outcome.error = f"{type(exc).__name__}: {exc}"
        outcome.score = {"pass": False}
    outcome.total_s = time.monotonic() - started
    return outcome


async def probe_reasoning_tolerance(model: str, key: str, bucket) -> str:
    """T0: does this model accept extra_body reasoning_effort? (N2 needs to know)."""
    client = AsyncOpenAI(base_url=NIM_OPENAI_URL, api_key=key)
    await bucket.acquire_wait()
    try:
        await client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": "ping"}],
            max_tokens=8,
            extra_body={"reasoning_effort": "none"},
        )
        return "accepted"
    except APIStatusError as exc:
        return f"rejected ({exc.status_code})"
    except (APIConnectionError, APIError) as exc:
        return f"inconclusive ({type(exc).__name__})"


# -- cache + report ----------------------------------------------------------------


def cache_path(model: str) -> Path:
    return RESULTS_DIR / (model.replace("/", "__") + ".json")


def load_cache(model: str) -> dict:
    path = cache_path(model)
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except ValueError:
            pass
    return {"model": model, "runs": {}, "reasoning_effort": None}


def save_cache(model: str, data: dict) -> None:
    RESULTS_DIR.mkdir(exist_ok=True)
    cache_path(model).write_text(
        json.dumps(data, indent=1, ensure_ascii=False), encoding="utf-8"
    )


def save_transcript(model: str, test_id: str, run_no: int, outcome: RunOutcome) -> None:
    TRANSCRIPT_DIR.mkdir(parents=True, exist_ok=True)
    name = f"{model.replace('/', '__')}__{test_id}__run{run_no}.txt"
    body = [f"model: {model}  test: {test_id}  run: {run_no}",
            f"score: {json.dumps(outcome.score, ensure_ascii=False)}", "calls:"]
    body += [f"  {n}({a})" for n, a in outcome.calls]
    body += ["final:", outcome.final, ""]
    (TRANSCRIPT_DIR / name).write_text("\n".join(body), encoding="utf-8")


def pct(values: list[bool]) -> float:
    return round(100.0 * sum(values) / len(values), 1) if values else 0.0


def percentile(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    ranked = sorted(values)
    return round(ranked[min(len(ranked) - 1, int(p * len(ranked)))], 2)


def summarize(model: str, data: dict, heavy: bool) -> dict:
    runs = data["runs"]
    all_outcomes = [o for outs in runs.values() for o in outs]
    firsts = [o["first_token_s"] for o in all_outcomes if o.get("first_token_s")]
    call_counts = sum(len(o["calls"]) for o in all_outcomes)
    valid_args = sum(o.get("valid_arg_calls", 0) for o in all_outcomes)

    def test_pct(tid):
        return pct([bool(o["score"].get("pass")) for o in runs.get(tid, [])])

    tool_pcts = [test_pct(t) for t in ("T1", "T2", "T3", "T4")]
    summary = {
        "model": model,
        "heavy_candidate": heavy,
        "reasoning_effort": data.get("reasoning_effort"),
        "T1_action": test_pct("T1"), "T2_chain": test_pct("T2"),
        "T3_args": test_pct("T3"), "T4_recovery": test_pct("T4"),
        "T5_discipline": test_pct("T5"), "T6_hindi": test_pct("T6"),
        "T7_hinglish": test_pct("T7"), "T8_json": test_pct("T8"),
        "T9_plan": test_pct("T9") if heavy else None,
        "arg_validity": round(100.0 * valid_args / call_counts, 1) if call_counts else 0.0,
        "first_token_p50": percentile(firsts, 0.5),
        "first_token_p95": percentile(firsts, 0.95),
        "count_429": sum(o.get("count_429", 0) for o in all_outcomes),
        "stream_errors": sum(o.get("stream_errors", 0) for o in all_outcomes),
        "failed_runs": sum(1 for o in all_outcomes if o.get("error")),
    }
    # Primary score: tool skill + discipline + language + json, light latency penalty.
    quality = statistics.mean(
        [*tool_pcts, summary["T5_discipline"], summary["T6_hindi"],
         summary["T7_hinglish"], summary["T8_json"]]
    )
    latency_penalty = min(20.0, (summary["first_token_p50"] or 0) * 4)
    summary["primary_score"] = round(quality - latency_penalty, 1)
    summary["heavy_score"] = (
        round(0.6 * (summary["T9_plan"] or 0) + 0.4 * quality, 1) if heavy else None
    )
    return summary


def write_report(summaries: list[dict], runs_per_test: int) -> None:
    by_primary = sorted(summaries, key=lambda s: -s["primary_score"])
    heavies = sorted(
        [s for s in summaries if s["heavy_candidate"]],
        key=lambda s: -(s["heavy_score"] or 0),
    )
    cols = ["model", "primary_score", "heavy_score", "T1_action", "T2_chain", "T3_args",
            "T4_recovery", "T5_discipline", "T6_hindi", "T7_hinglish", "T8_json",
            "T9_plan", "arg_validity", "first_token_p50", "first_token_p95",
            "count_429", "stream_errors", "reasoning_effort"]
    lines = [
        "# NIM model shootout — Phase N1 report",
        "",
        f"N={runs_per_test} runs per test per model, run at evening IST "
        "(peak congestion), Baby's real tool schemas from tools/registry.py.",
        "The script recommends; **Tanishq picks the winners** (config.yaml + "
        "DECISIONS.md).",
        "",
        "| " + " | ".join(cols) + " |",
        "|" + "---|" * len(cols),
    ]
    for s in by_primary:
        lines.append("| " + " | ".join(str(s.get(c, "")) for c in cols) + " |")
    lines += [
        "",
        f"**Recommended primary:** `{by_primary[0]['model']}` "
        f"(score {by_primary[0]['primary_score']})",
        f"**Recommended heavy:** `{heavies[0]['model']}` "
        f"(score {heavies[0]['heavy_score']})" if heavies else "",
        "",
        "Column notes: T-columns are pass-% over runs; arg_validity is "
        "parseable-JSON tool args over all calls; first_token in seconds; "
        "reasoning_effort is whether the model accepted the extra_body knob "
        "(T0 probe — informs the N2 router). Transcripts (T7 human judgment) "
        "in bench_results/transcripts/.",
    ]
    RESULTS_DIR.mkdir(exist_ok=True)
    (RESULTS_DIR / "REPORT.md").write_text("\n".join(lines), encoding="utf-8")


# -- main -------------------------------------------------------------------------


async def bench(models: list[str], heavy: set[str], runs_per_test: int) -> None:
    key = os.environ.get("NVIDIA_API_KEY", "")
    if not key.startswith("nvapi-"):
        print("FAIL: NVIDIA_API_KEY missing from .env")
        raise SystemExit(1)

    import tools as tool_pkg
    from tools import registry

    tool_pkg.register_all()
    tools = registry.schemas()
    print(f"tool schemas: {len(tools)} (live from tools/registry.py)")

    bucket = TokenBucket(rpm=36)
    summaries = []
    for model in models:
        data = load_cache(model)
        provider = NvidiaProvider(model=model, api_key=key)
        print(f"\n=== {model} ===")
        if data.get("reasoning_effort") is None:
            data["reasoning_effort"] = await probe_reasoning_tolerance(model, key, bucket)
            save_cache(model, data)
            print(f"  T0 reasoning_effort probe: {data['reasoning_effort']}")
        for case in CASES:
            if case.heavy_only and model not in heavy:
                continue
            done = data["runs"].setdefault(case.id, [])
            while len(done) < runs_per_test:
                run_no = len(done) + 1
                outcome = await run_case(provider, case, tools, bucket)
                done.append(outcome.to_json())
                save_cache(model, data)  # crash-safe resume after every run
                save_transcript(model, case.id, run_no, outcome)
                flag = "PASS" if outcome.score.get("pass") else "fail"
                extra = f" [{outcome.error}]" if outcome.error else ""
                print(f"  {case.id} run {run_no}/{runs_per_test}: {flag}"
                      f" ({outcome.total_s:.1f}s){extra}")
        summaries.append(summarize(model, data, model in heavy))

    write_report(summaries, runs_per_test)
    print(f"\nreport: {RESULTS_DIR / 'REPORT.md'}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="NIM model shootout (Phase N1)")
    parser.add_argument("--models", default="", help="comma-separated catalog IDs")
    parser.add_argument("--heavy", default="", help="comma-separated heavy-slot IDs")
    parser.add_argument("--runs", type=int, default=5, help="runs per test (default 5)")
    args = parser.parse_args()
    model_list = [m.strip() for m in args.models.split(",") if m.strip()] or DEFAULT_MODELS
    heavy_set = {m.strip() for m in args.heavy.split(",") if m.strip()} or DEFAULT_HEAVY
    asyncio.run(bench(model_list, heavy_set, args.runs))
