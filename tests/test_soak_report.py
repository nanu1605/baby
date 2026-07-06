"""N4: soak_report parses the router's audit rows into the PR summary."""

from __future__ import annotations

import importlib.util
import json
import sqlite3
import sys
from pathlib import Path

_SPEC = importlib.util.spec_from_file_location(
    "soak_report", Path(__file__).parent.parent / "scripts" / "soak_report.py"
)
soak = importlib.util.module_from_spec(_SPEC)
sys.modules["soak_report"] = soak
_SPEC.loader.exec_module(soak)


def _make_db(tmp_path):
    db = tmp_path / "soak.db"
    con = sqlite3.connect(db)
    con.execute(
        "CREATE TABLE audit_log (id INTEGER PRIMARY KEY, ts TEXT, channel TEXT, "
        "tool TEXT, args TEXT, safety_class TEXT, approved INTEGER, result_summary TEXT)"
    )
    rows = [
        ("2026-07-06 10:00:00", "route nim_primary", "normal turn"),
        ("2026-07-06 10:00:02", "served nim_primary",
         json.dumps({"channel": "ui", "first_token_ms": 1200.0})),
        ("2026-07-06 10:05:00", "route nim_primary", "normal turn"),
        ("2026-07-06 10:05:04", "skip nim_primary", "first token > 3.5s"),
        ("2026-07-06 10:05:04", "state cloud->degraded", "timeout"),
        ("2026-07-06 10:05:05", "route daily", "fallback from nim_primary"),
        ("2026-07-06 10:05:09", "served daily",
         json.dumps({"channel": "voice", "first_token_ms": 4100.0})),
        ("2026-07-06 10:20:00", "state degraded->cloud", "recovered"),
        ("2026-07-06 10:25:00", "skip backstop", "HTTP 429"),
        ("2026-07-05 09:00:00", "route daily", "before the window"),  # filtered by since
    ]
    con.executemany(
        "INSERT INTO audit_log (ts, channel, tool, args, safety_class, approved, "
        "result_summary) VALUES (?, 'router', 'router', ?, 'allow', 1, ?)",
        [(ts, json.dumps({"action": action}), detail) for ts, action, detail in rows],
    )
    con.commit()
    con.close()
    return str(db)


def test_collect_and_render(tmp_path):
    data = soak.collect(_make_db(tmp_path), "2026-07-06")
    assert data["routes"] == {"nim_primary": 2, "daily": 1}
    assert data["fallbacks"] == 1
    assert data["skips"] == {"timeout": 1, "429": 1}
    assert len(data["transitions"]) == 2
    assert data["served"]["nim_primary"] == [1200.0]
    assert data["voice_deadair"] == 1  # 4100 ms voice first token

    report = soak.render(data, "2026-07-06", tracebacks=0)
    assert "| nim_primary | 2 | 1200.0 ms" in report
    assert "timeout: 1" in report and "429: 1" in report
    assert "**0** (target: 0)" in report


def test_classify_skip_buckets():
    assert soak.classify_skip("first token > 3.5s") == "timeout"
    assert soak.classify_skip("HTTP 429") == "429"
    assert soak.classify_skip("rate bucket empty — overflow to local") == "overflow"
    assert soak.classify_skip("cooldown/unhealthy") == "cooldown"
    assert soak.classify_skip("connection failed") == "connection"
    assert soak.classify_skip("weird") == "other"


def test_traceback_count(tmp_path):
    log = tmp_path / "baby.log"
    log.write_text(
        "boot ok\nTraceback (most recent call last):\n  ...\nboot ok\n"
        "Traceback (most recent call last):\n  ...\n",
        encoding="utf-8",
    )
    assert soak.count_tracebacks(log) == 2
    assert soak.count_tracebacks(tmp_path / "missing.log") == 0
