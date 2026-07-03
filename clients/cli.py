"""REPL client — Phase 0 debug surface."""

from __future__ import annotations

import sys

import yaml

from core.agent import AgentCore
from core.providers.ollama import OllamaProvider
from db.database import Database


async def run_cli(config_path: str = "config.yaml") -> None:
    with open(config_path, encoding="utf-8") as f:
        config = yaml.safe_load(f)

    daily = config["models"]["daily"]
    provider = OllamaProvider(
        model=daily["model"],
        temperature=daily.get("temperature", 0.7),
        keep_alive=daily.get("keep_alive", "24h"),
        num_ctx=daily.get("num_ctx", 8192),
    )

    db = Database("baby.db")
    await db.connect()

    if not await provider.healthy():
        print("Baby could not start: Ollama is not reachable at 127.0.0.1:11434.")
        print("Start it with:  ollama serve   (or launch the Ollama app)")
        await db.close()
        sys.exit(1)

    # Readiness: 1-token warm-up ping loads the model into VRAM, then verify
    # the served context — Ollama truncates silently when it's too small.
    print("warming up model...", flush=True)
    async for _ in provider.chat([{"role": "user", "content": "ping"}], max_tokens=1):
        pass
    ctx = await provider.loaded_context_length()
    if ctx is not None and ctx < provider.num_ctx:
        print(
            f"warning: Ollama is serving a {ctx}-token context but config wants "
            f"{provider.num_ctx}. Set OLLAMA_CONTEXT_LENGTH={provider.num_ctx} and "
            "restart Ollama (scripts/setup.ps1 does both)."
        )

    # Resume the latest CLI conversation so restarts keep the thread.
    conv_id = await db.latest_conversation("cli")
    if conv_id is None:
        conv_id = await db.create_conversation("cli")
        print(f"[new conversation #{conv_id}]")
    else:
        print(f"[resuming conversation #{conv_id}]")

    agent = AgentCore(provider, db, conv_id)
    print(f"Baby ready (text only) — model: {daily['model']}. Ctrl+C or 'exit' to quit.\n")

    try:
        while True:
            try:
                user_text = input("you> ").strip()
            except EOFError:
                break
            if not user_text:
                continue
            if user_text.lower() in ("exit", "quit"):
                break
            print("baby> ", end="", flush=True)
            await agent.run_turn(
                user_text,
                on_delta=lambda d: print(d, end="", flush=True),
                on_event=lambda e: print(f"\n  [{e}]", end="", flush=True),
            )
            print("\n")
    except KeyboardInterrupt:
        print("\nbye.")
    finally:
        await db.close()
