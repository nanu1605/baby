"""Registry contract: schemas, descriptions, dispatch robustness."""

from __future__ import annotations

import json

from tools import register_all, registry


def setup_module():
    register_all()


def test_all_phase1_tools_registered():
    names = {s["function"]["name"] for s in registry.schemas()}
    expected = {
        "get_time",
        "get_system_stats",
        "app_control",
        "file_search",
        "read_file",
        "write_file",
        "run_shell",
        "web_search",
        "fetch_page",
    }
    assert expected <= names


def test_descriptions_within_context_budget():
    for schema in registry.schemas():
        fn = schema["function"]
        words = len(fn["description"].split())
        assert words <= 25, f"{fn['name']} description too long ({words} words)"


def test_optional_params_not_required():
    schema = next(s["function"] for s in registry.schemas() if s["function"]["name"] == "read_file")
    assert schema["parameters"]["required"] == ["path"]
    assert "max_kb" in schema["parameters"]["properties"]


async def test_dispatch_missing_required_arg_is_error():
    result = json.loads(await registry.dispatch("read_file", "{}"))
    assert "error" in result


async def test_dispatch_invalid_json_is_error():
    result = json.loads(await registry.dispatch("get_time", "{not json"))
    assert "error" in result


async def test_dispatch_dict_result_serializes():
    result = await registry.dispatch("get_system_stats", "{}")
    parsed = json.loads(result)
    assert "cpu_percent" in parsed
