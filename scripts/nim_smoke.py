"""Phase N0 acceptance: one live NIM call — streamed text + a valid tool call.

Usage:
    uv run python scripts/nim_smoke.py --model meta/llama-3.3-70b-instruct

Requires NVIDIA_API_KEY in .env (nvapi- prefix). Any catalog model works;
this proves the provider wire (auth, streaming, tool passthrough), not model
quality — that's the N1 bench's job. Never prints the key.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env")

from core.providers.nvidia import NvidiaProvider  # noqa: E402

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "get_system_stats",
            "description": "Report CPU, RAM and GPU usage of the PC.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    }
]


async def main(model: str) -> int:
    key = os.environ.get("NVIDIA_API_KEY", "")
    if not key.startswith("nvapi-"):
        print("FAIL: NVIDIA_API_KEY missing from .env (expected nvapi- prefix)")
        return 1
    provider = NvidiaProvider(model=model, api_key=key)

    print("[1/3] probe: GET /v1/models ... ", end="", flush=True)
    if not await provider.probe():
        print("FAIL (connectivity/auth)")
        return 1
    print("ok")

    print(f"[2/3] streamed completion via {model} ... ", end="", flush=True)
    text = ""
    async for chunk in provider.chat(
        [{"role": "user", "content": "Reply with one short sentence: what is NVIDIA NIM?"}],
        max_tokens=100,
    ):
        text += chunk.delta
    if not text.strip():
        print("FAIL (empty reply)")
        return 1
    print(f"ok\n      reply: {text.strip()[:120]}")

    print("[3/3] tool call ... ", end="", flush=True)
    calls = []
    async for chunk in provider.chat(
        [{"role": "user", "content": "Check my CPU usage using the available tool."}],
        tools=TOOLS,
        max_tokens=200,
    ):
        calls.extend(chunk.tool_calls)
    if not calls or calls[0].name != "get_system_stats":
        got = [c.name for c in calls] or "none"
        print(f"FAIL (expected get_system_stats call, got: {got})")
        return 1
    print(f"ok\n      call: {calls[0].name}({calls[0].arguments or '{}'})")
    print("\nN0 acceptance PASSED")
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", required=True, help="exact NIM catalog model ID")
    args = parser.parse_args()
    raise SystemExit(asyncio.run(main(args.model)))
