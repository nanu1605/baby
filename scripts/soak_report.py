"""N4 soak report: routing stability metrics from audit_log + the boot log.

Usage:
    uv run python scripts/soak_report.py --since 2026-07-06
    uv run python scripts/soak_report.py --since 2026-07-06 --db baby.db

Reads the router's own audit rows (route / skip / state / served — durable
across restarts) and greps Baby's log for tracebacks. Prints markdown for
the PR description. No new storage: the audit trail IS the soak record.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
from collections import Counter, defaultdict
from pathlib import Path

VOICE_DEADAIR_MS = 3500.0


def percentile(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    ranked = sorted(values)
    return round(ranked[min(len(ranked) - 1, int(p * len(ranked)))], 1)


def classify_skip(detail: str) -> str:
    """Bucket a skip reason line for the report table."""
    lowered = detail.lower()
    if "429" in lowered:
        return "429"
    if "first token" in lowered or "timeout" in lowered:
        return "timeout"
    if "overflow" in lowered or "bucket" in lowered:
        return "overflow"
    if "cooldown" in lowered or "unhealthy" in lowered:
        return "cooldown"
    if "connection" in lowered or "dns" in lowered:
        return "connection"
    return "other"


def collect(db_path: str, since: str) -> dict:
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    rows = con.execute(
        "SELECT ts, args, result_summary FROM audit_log "
        "WHERE tool = 'router' AND ts >= ? ORDER BY id",
        (since,),
    ).fetchall()
    con.close()

    routes: Counter = Counter()
    fallbacks = 0
    skips: Counter = Counter()
    transitions: list[str] = []
    served: dict[str, list[float]] = defaultdict(list)
    voice_deadair = 0

    for row in rows:
        action = json.loads(row["args"]).get("action", "")
        detail = row["result_summary"] or ""
        if action.startswith("route "):
            routes[action.removeprefix("route ")] += 1
            if "fallback" in detail:
                fallbacks += 1
        elif action.startswith("skip "):
            skips[classify_skip(detail)] += 1
        elif action.startswith("state "):
            transitions.append(f"{row['ts']}  {action.removeprefix('state ')}  ({detail})")
        elif action.startswith("served "):
            tier = action.removeprefix("served ")
            try:
                data = json.loads(detail)
            except ValueError:
                continue
            ms = data.get("first_token_ms")
            if ms is not None:
                served[tier].append(float(ms))
                if data.get("channel") == "voice" and ms > VOICE_DEADAIR_MS:
                    voice_deadair += 1
    return {
        "routes": routes, "fallbacks": fallbacks, "skips": skips,
        "transitions": transitions, "served": served, "voice_deadair": voice_deadair,
    }


def count_tracebacks(log_path: Path) -> int:
    if not log_path.exists():
        return 0
    text = log_path.read_text(encoding="utf-8", errors="replace")
    return len(re.findall(r"^Traceback \(most recent call last\):", text, re.MULTILINE))


def render(data: dict, since: str, tracebacks: int) -> str:
    lines = [
        f"## Soak summary (cloud_primary, since {since})",
        "",
        "| brain | turns | first-token p50 | p95 |",
        "|---|---|---|---|",
    ]
    for tier, count in data["routes"].most_common():
        samples = data["served"].get(tier, [])
        p50 = f"{percentile(samples, 0.5)} ms" if samples else "n/a"
        p95 = f"{percentile(samples, 0.95)} ms" if samples else "n/a"
        lines.append(f"| {tier} | {count} | {p50} | {p95} |")
    lines += [
        "",
        f"- Fallback routes (rung dropped mid-turn): **{data['fallbacks']}**",
        "- Skips by reason: "
        + (", ".join(f"{k}: {v}" for k, v in data["skips"].most_common()) or "none"),
        f"- Health-state transitions: **{len(data['transitions'])}**",
        f"- Voice dead-air events (first token > {VOICE_DEADAIR_MS:.0f} ms on voice): "
        f"**{data['voice_deadair']}**",
        f"- Unhandled exceptions in baby.log: **{tracebacks}** (target: 0)",
    ]
    if data["transitions"]:
        lines += ["", "<details><summary>state transitions</summary>", ""]
        lines += [f"    {t}" for t in data["transitions"]]
        lines += ["", "</details>"]
    return "\n".join(lines)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="NIM soak report from audit_log")
    parser.add_argument("--since", required=True, help="ISO date, e.g. 2026-07-06")
    parser.add_argument("--db", default="baby.db")
    parser.add_argument(
        "--log",
        default=os.path.expandvars(r"%LOCALAPPDATA%\baby\logs\baby.log"),
    )
    args = parser.parse_args()
    stats = collect(args.db, args.since)
    print(render(stats, args.since, count_tracebacks(Path(args.log))))
