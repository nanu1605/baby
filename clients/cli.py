"""REPL client — consumes the same event bus as the web UI."""

from __future__ import annotations

import asyncio
import sys

import yaml

from core.agent import AgentCore
from core.bus import EventBus
from core.readiness import ready_check
from core.safety import SafetyConfig, SafetyGate
from db.database import Database

_YES = {"y", "yes", "haan", "haa", "ha", "kar do", "kardo", "go ahead", "ok"}


def load_config(config_path: str = "config.yaml") -> dict:
    with open(config_path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def build_gate(config: dict, bus: EventBus) -> SafetyGate:
    safety = config.get("safety", {})
    cfg = SafetyConfig(
        mode=safety.get("mode", "enforce"),
        auto_allow_app_close=tuple(safety.get("auto_allow_app_close", [])),
        confirm_timeout_s=float(safety.get("confirm_timeout_s", 60)),
    )
    return SafetyGate(cfg, bus)


async def _render(bus: EventBus, gate: SafetyGate) -> None:
    """Bus consumer: stream tokens, show tool activity, answer confirmations."""
    q = bus.subscribe()
    try:
        while True:
            event = await q.get()
            if event.channel != "cli":
                continue
            p = event.payload
            if event.kind == "token":
                print(p["text"], end="", flush=True)
            elif event.kind == "tool_start":
                print(f"\n  [{p['safety_class']}] {p['tool']}({p['args']})", flush=True)
            elif event.kind == "tool_end":
                print(f"  -> {p['status']}: {p['result_summary'][:160]}", flush=True)
            elif event.kind == "confirm_request":
                prompt = (
                    f"\nCONFIRM: {p['command']}\n  ({p['explanation']})"
                    f"\n  run this? [y/N] ({int(p['timeout_s'])}s): "
                )
                answer = await asyncio.to_thread(input, prompt)
                gate.confirmations.resolve(p["confirm_id"], answer.strip().lower() in _YES)
    except asyncio.CancelledError:
        bus.unsubscribe(q)
        raise


async def run_cli(config_path: str = "config.yaml") -> None:
    from core.router import build_provider

    config = load_config(config_path)
    daily = config["models"]["daily"]
    db = Database("baby.db")
    await db.connect()
    bus = EventBus()
    provider = build_provider(config, bus=bus, db=db)

    ok, notes = await ready_check(provider, db)
    for note in notes:
        print(note)
    if not ok:
        await db.close()
        sys.exit(1)

    from memory import build_memory

    memory = await build_memory(config, db, provider)
    if memory is not None:
        print(f"memory ready ({await memory.store.count_active()} facts)")

    conv_id = await db.latest_conversation("cli")
    if conv_id is None:
        conv_id = await db.create_conversation("cli")
        print(f"[new conversation #{conv_id}]")
    else:
        print(f"[resuming conversation #{conv_id}]")

    gate = build_gate(config, bus)
    agent = AgentCore(
        provider,
        db,
        conv_id,
        channel="cli",
        bus=bus,
        gate=gate,
        memory=memory,
        suggest_next_step=config.get("persona", {}).get("suggest_next_step", True),
    )
    renderer = asyncio.create_task(_render(bus, gate))

    print(f"Baby ready (text only) — model: {daily['model']}. Ctrl+C or 'exit' to quit.\n")
    try:
        while True:
            try:
                user_text = await asyncio.to_thread(input, "you> ")
            except EOFError:
                break
            user_text = user_text.strip()
            if not user_text:
                continue
            if user_text.lower() in ("exit", "quit"):
                break
            print("baby> ", end="", flush=True)
            await agent.run_turn(user_text)
            await asyncio.sleep(0.05)  # let the renderer drain trailing events
            print("\n")
    except KeyboardInterrupt:
        print("\nbye.")
    finally:
        renderer.cancel()
        if agent.maintenance_task is not None and not agent.maintenance_task.done():
            try:
                await asyncio.wait_for(agent.maintenance_task, timeout=30)
            except Exception:  # noqa: BLE001 — shutdown is best-effort
                pass
        await db.close()
