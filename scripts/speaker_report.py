"""B6/B7 speaker-verification FAR/FRR report from the audit trail.

Every scored voice utterance is logged as an audit row (tool='speaker_verify',
args = {score, tier, decision, model, source, mode}) by voice/pipeline.py. This
turns that trail into per-model false-reject / false-accept curves so the B7 soak
picks the winning model + threshold from data, not vibes.

Ground truth comes from WHO was speaking in each time window (the audit can't know
that on its own):
  * --since / --until  = the OWNER soak window  → every row is a true-owner
                         utterance → FRR = fraction that would be rejected.
  * --far-since / --far-until (optional) = a window when a NON-owner was
                         deliberately speaking → FAR = fraction that would be
                         accepted.

Usage:
    uv run python scripts/speaker_report.py --since 2026-07-10
    uv run python scripts/speaker_report.py --since 2026-07-10 \
        --far-since 2026-07-12T14:00 --far-until 2026-07-12T14:30

Prints markdown for the PR. No new storage — the audit trail IS the record.
Targets (spec): owner FRR <= 2% AND 0 non-owner accepted at the chosen threshold.
If no model+threshold clears both, ship the feature OFF and record findings.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from collections import defaultdict

TARGET_FRR = 0.02
# Threshold grid swept for each model (accept_threshold candidates).
GRID = [round(0.30 + 0.025 * i, 3) for i in range(19)]  # 0.30 .. 0.75


def _collect(db_path: str, since: str, until: str | None) -> dict[str, list[float]]:
    """model -> list of utterance scores in [since, until]."""
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    sql = "SELECT args FROM audit_log WHERE tool = 'speaker_verify' AND ts >= ?"
    params: list = [since]
    if until:
        sql += " AND ts <= ?"
        params.append(until)
    rows = con.execute(sql + " ORDER BY id", params).fetchall()
    con.close()

    by_model: dict[str, list[float]] = defaultdict(list)
    for row in rows:
        try:
            d = json.loads(row["args"])
        except ValueError:
            continue
        score = d.get("score")
        if score is None:
            continue
        by_model[d.get("model") or "?"].append(float(score))
    return by_model


def _frac_below(scores: list[float], t: float) -> float:
    return sum(1 for s in scores if s < t) / len(scores) if scores else 0.0


def _frac_atleast(scores: list[float], t: float) -> float:
    return sum(1 for s in scores if s >= t) / len(scores) if scores else 0.0


def _percentile(scores: list[float], p: float) -> float:
    if not scores:
        return 0.0
    ranked = sorted(scores)
    return round(ranked[min(len(ranked) - 1, int(p * len(ranked)))], 3)


def render(
    owner: dict[str, list[float]], far: dict[str, list[float]], since: str
) -> str:
    lines = [
        f"## Speaker-verify FAR/FRR report (since {since})",
        "",
        f"Targets: owner FRR ≤ {TARGET_FRR:.0%} AND 0 non-owner accepted.",
    ]
    models = sorted(set(owner) | set(far))
    if not models:
        lines += ["", "_No speaker_verify audit rows in the window._"]
        return "\n".join(lines)

    for model in models:
        o = owner.get(model, [])
        f = far.get(model, [])
        lines += [
            "",
            f"### {model}",
            f"- owner utterances: **{len(o)}**"
            + (f" (score p05 {_percentile(o, 0.05)}, p50 {_percentile(o, 0.5)})" if o else ""),
            f"- non-owner utterances: **{len(f)}**"
            + (f" (score p50 {_percentile(f, 0.5)}, p95 {_percentile(f, 0.95)})" if f else ""),
            "",
            "| threshold | owner FRR | non-owner FAR |",
            "|---|---|---|",
        ]
        best: float | None = None
        for t in GRID:
            frr = _frac_below(o, t) if o else None
            faR = _frac_atleast(f, t) if f else None
            frr_c = f"{frr:.1%}" if frr is not None else "n/a"
            far_c = f"{faR:.1%}" if faR is not None else "n/a"
            mark = ""
            if o and frr is not None and frr <= TARGET_FRR and (not f or faR == 0.0):
                if best is None:
                    best = t  # lowest threshold that clears both targets
                mark = "  ← clears targets"
            lines.append(f"| {t:.3f} | {frr_c} | {far_c} |{mark}")
        if best is not None:
            lines.append("")
            lines.append(
                f"**Recommended accept_threshold for {model}: {best:.3f}** "
                f"(FRR ≤ {TARGET_FRR:.0%}, 0 non-owner accepted). Set reject_threshold "
                f"a little below it (e.g. {max(0.2, round(best - 0.15, 3))})."
            )
        elif o:
            lines.append("")
            lines.append(
                f"**No threshold clears both targets for {model}** — ship OFF for this "
                "model, or record more coverage / re-enrol before enabling."
            )
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Speaker-verify FAR/FRR report")
    parser.add_argument("--since", required=True, help="owner window start (ISO date)")
    parser.add_argument("--until", default=None, help="owner window end (ISO, optional)")
    parser.add_argument("--far-since", default=None, help="non-owner window start (ISO)")
    parser.add_argument("--far-until", default=None, help="non-owner window end (ISO)")
    parser.add_argument("--db", default="baby.db")
    args = parser.parse_args()

    owner = _collect(args.db, args.since, args.until)
    far: dict[str, list[float]] = {}
    if args.far_since:
        far = _collect(args.db, args.far_since, args.far_until)
    print(render(owner, far, args.since))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
