"""Phase N1: bench scoring helpers + token bucket (all offline)."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

from core.prompts import devanagari_ratio, hinglish_hits
from core.ratelimit import TokenBucket

_SPEC = importlib.util.spec_from_file_location(
    "pick_nim_model", Path(__file__).parent.parent / "scripts" / "pick_nim_model.py"
)
bench = importlib.util.module_from_spec(_SPEC)
sys.modules["pick_nim_model"] = bench
_SPEC.loader.exec_module(bench)


# -- language heuristics -----------------------------------------------------------


def test_devanagari_ratio():
    assert devanagari_ratio("आज मौसम अच्छा है") == 1.0
    assert devanagari_ratio("hello world") == 0.0
    assert devanagari_ratio("12345 !!!") == 0.0
    mixed = devanagari_ratio("ok तो ठीक है boss")
    assert 0.3 < mixed < 1.0


def test_hinglish_hits():
    assert hinglish_hits("yaar kya chal raha hai bhai") >= 3
    assert hinglish_hits("the quick brown fox") == 0


# -- scoring -----------------------------------------------------------------------


def test_score_t1_pass_and_fail():
    calls = [("app_control", {"action": "close", "name": "chrome"}),
             ("get_system_stats", {})]
    assert bench.score_t1(calls, "CPU is at 34.2%")["pass"]
    assert not bench.score_t1([("get_system_stats", {})], "34")["pass"]
    wrong = [("app_control", {"action": "open", "name": "chrome"}), ("get_system_stats", {})]
    assert not bench.score_t1(wrong, "34")["pass"]


def test_score_t2_requires_order_and_exact_path():
    good = [
        ("file_search", {"query": "quarterly_report.pdf"}),
        ("read_file", {"path": "C:\\Users\\tanis\\Documents\\quarterly_report.pdf"}),
    ]
    assert bench.score_t2(good, "Revenue was $4.2M")["pass"]
    assert not bench.score_t2(list(reversed(good)), "$4.2M")["chain_ordered"]
    bad_path = [good[0], ("read_file", {"path": "C:\\other.pdf"})]
    assert not bench.score_t2(bad_path, "$4.2M")["pass"]


def test_score_t3_exact_args():
    assert bench.score_t3([("file_search", {"query": "backup .py", "max_results": 5})], "")["pass"]
    assert not bench.score_t3([("file_search", {"query": "backup"})], "")["limit_ok"]


def test_score_t4_honesty():
    assert bench.score_t4([("app_control", {"action": "open", "name": "spotify"})],
                          "Spotify is not installed on this PC.")["pass"]
    assert not bench.score_t4([("app_control", {})], "Done! I opened Spotify for you.")["pass"]


def test_score_t5_discipline():
    assert bench.score_t5([], "bas yaar, sab badhiya!")["pass"]
    assert not bench.score_t5([("get_system_stats", {})], "sab theek")["pass"]


def test_score_t6_t7_language():
    assert bench.score_t6([], "कल का दिन शानदार होगा, मेहनत करते रहो।")["pass"]
    assert not bench.score_t6([], "Tomorrow will be great!")["pass"]
    assert bench.score_t7([], "ghar pe movie dekho yaar, pizza mangao aur chill karo")["pass"]
    assert not bench.score_t7([], "Watch a movie and order pizza.")["pass"]


def test_score_t8_json():
    assert bench.score_t8([], 'Sure: {"city": "Indore", "why": "poha"}')["pass"]
    assert not bench.score_t8([], "Indore, because poha")["pass"]
    assert not bench.score_t8([], '{"city": "Indore"}')["pass"]


def test_score_t9_uses_orchestrator_parser():
    plan = ('{"subtasks": [{"title": "Backend", "spec": "Build the API."},'
            '{"title": "Frontend", "spec": "Build the form."}]}')
    assert bench.score_t9([], plan)["pass"]
    assert not bench.score_t9([], "I would split it into parts.")["pass"]
    one = '{"subtasks": [{"title": "All", "spec": "Everything."}]}'
    assert not bench.score_t9([], one)["pass"]  # 2..4 required


def test_parse_args_and_extract_json():
    assert bench.parse_args_json('{"a": 1}') == {"a": 1}
    assert bench.parse_args_json("") == {}
    assert bench.parse_args_json("not json") is None
    assert bench.parse_args_json('["list"]') is None
    assert bench.extract_json_object('prose {"k": "v"} more') == {"k": "v"}
    assert bench.extract_json_object("no braces") is None


# -- token bucket -------------------------------------------------------------------


def test_bucket_caps_at_rpm():
    bucket = TokenBucket(rpm=3)
    assert all(bucket.try_acquire() for _ in range(3))
    assert not bucket.try_acquire()
    assert bucket.seconds_until_slot() > 0


def test_bucket_background_share():
    bucket = TokenBucket(rpm=4, background_share=0.5)
    assert bucket.try_acquire(background=True)
    assert bucket.try_acquire(background=True)
    assert not bucket.try_acquire(background=True)  # 2/4 = 50% share exhausted
    assert bucket.try_acquire()  # interactive still has headroom
    assert bucket.try_acquire()
    assert not bucket.try_acquire()  # total window full


def test_bucket_window_slides():
    bucket = TokenBucket(rpm=1)
    assert bucket.try_acquire()
    assert not bucket.try_acquire()
    bucket._stamps[0] = (bucket._stamps[0][0] - 61.0, False)  # age the stamp out
    assert bucket.try_acquire()


@pytest.mark.asyncio
async def test_bucket_acquire_wait_takes_freed_slot():
    bucket = TokenBucket(rpm=1)
    assert bucket.try_acquire()
    bucket._stamps[0] = (bucket._stamps[0][0] - 59.9, False)  # frees almost now
    await bucket.acquire_wait()
    assert len(bucket._stamps) == 1
