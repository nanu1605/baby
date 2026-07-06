"""Tool registry: @tool decorator, OpenAI schema export, safe dispatch."""

from __future__ import annotations

import asyncio
import inspect
import json
from collections.abc import Callable
from typing import Any, get_type_hints

_PY_TO_JSON = {str: "string", int: "integer", float: "number", bool: "boolean"}

_TOOLS: dict[str, dict] = {}


def tool(func: Callable) -> Callable:
    """Register a function as a model-callable tool.

    Schema is generated from type hints; description is the first
    docstring line (keep it ≤ 25 words — context budget).
    """
    sig = inspect.signature(func)
    hints = get_type_hints(func)
    properties: dict[str, Any] = {}
    required: list[str] = []
    for pname, param in sig.parameters.items():
        ptype = hints.get(pname, str)
        properties[pname] = {"type": _PY_TO_JSON.get(ptype, "string")}
        if param.default is inspect.Parameter.empty:
            required.append(pname)
    description = (func.__doc__ or func.__name__).strip().splitlines()[0]
    _TOOLS[func.__name__] = {
        "func": func,
        "schema": {
            "type": "function",
            "function": {
                "name": func.__name__,
                "description": description,
                "parameters": {
                    "type": "object",
                    "properties": properties,
                    "required": required,
                },
            },
        },
    }
    return func


def schemas() -> list[dict]:
    """OpenAI-format tool schemas for every registered tool."""
    return [entry["schema"] for entry in _TOOLS.values()]


def _finalize(result: object) -> str:
    """Enforce the tool contract: every result is a non-empty string or JSON.

    core/agent.py counts any result not prefixed ``{"error"`` as a success, so
    a ``None`` / ``""`` / ``{}`` return used to read as a silent win and reach
    the user as "(no response)". One wrapper closes that hole for the whole
    tool surface: empty returns become a structured error the model can react
    to. Non-empty lists/objects (e.g. "no search matches" → ``[]``) pass
    through untouched — those are data, not silence.
    """
    if isinstance(result, str):
        return result if result.strip() else json.dumps({"error": "tool returned no data"})
    if result is None or result == {}:
        return json.dumps({"error": "tool returned no data"})
    return json.dumps(result)


async def dispatch(name: str, arguments: str) -> str:
    """Run a tool by name with JSON-string arguments.

    Never raises: unknown tools, bad args, and tool exceptions all come
    back as {"error": "..."} so the model can react.
    """
    entry = _TOOLS.get(name)
    if entry is None:
        return json.dumps({"error": f"unknown tool: {name}"})
    try:
        kwargs = json.loads(arguments) if arguments.strip() else {}
    except json.JSONDecodeError as exc:
        return json.dumps({"error": f"invalid arguments JSON: {exc}"})
    func = entry["func"]
    try:
        if inspect.iscoroutinefunction(func):
            result = await func(**kwargs)
        else:
            # Sync tools run off the event loop — a blocking tool (network,
            # subprocess) must not stall streaming and other channels.
            result = await asyncio.to_thread(func, **kwargs)
        if asyncio.iscoroutine(result):
            result = await result
        return _finalize(result)
    except Exception as exc:  # noqa: BLE001 — tools must never kill the loop
        return json.dumps({"error": f"{type(exc).__name__}: {exc}"})
