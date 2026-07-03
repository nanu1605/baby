"""FastAPI web UI: chat + live activity feed, bound to 127.0.0.1 only.

The UI is a thin client: it renders the same event stream every other
surface consumes and submits messages to the same AgentCore.
"""

from __future__ import annotations

import asyncio
import sys
from dataclasses import dataclass, field
from pathlib import Path

import uvicorn
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from core.agent import AgentCore
from core.bus import EventBus
from core.safety import SafetyGate
from db.database import Database

WEB_DIR = Path(__file__).parent / "web"

_CHAT_KINDS = {"turn_start", "token", "turn_end", "error"}
_ACTIVITY_KINDS = {"tool_start", "tool_end", "confirm_request", "confirm_resolved", "status"}


@dataclass
class UIContext:
    db: Database
    bus: EventBus
    gate: SafetyGate
    agent: AgentCore
    config: dict = field(default_factory=dict)
    memory: object | None = None  # memory.Memory; kept loose to avoid heavy import
    current_turn: asyncio.Task | None = None

    def turn_running(self) -> bool:
        return self.current_turn is not None and not self.current_turn.done()


def create_app(ctx: UIContext) -> FastAPI:
    app = FastAPI(title="Baby", docs_url=None, redoc_url=None)
    app.mount("/static", StaticFiles(directory=WEB_DIR), name="static")

    @app.get("/")
    async def index():
        return FileResponse(WEB_DIR / "index.html")

    @app.get("/stats")
    async def stats():
        from tools.system_stats import snapshot

        data = await asyncio.to_thread(snapshot)
        data["model"] = ctx.config.get("models", {}).get("daily", {}).get("model", "?")
        data["turn_running"] = ctx.turn_running()
        return data

    @app.get("/history")
    async def history(limit: int = 50):
        return await ctx.db.get_history(ctx.agent.conversation_id, limit)

    @app.get("/memory")
    async def memory_view(limit: int = 200):
        if ctx.memory is None:
            return []
        return await ctx.memory.store.list_facts(limit)

    @app.post("/confirm/{confirm_id}")
    async def confirm(confirm_id: str, body: dict):
        approved = bool(body.get("approved", False))
        if not ctx.gate.confirmations.resolve(confirm_id, approved):
            return JSONResponse({"error": "unknown or expired confirmation"}, status_code=404)
        return {"ok": True}

    @app.post("/kill")
    async def kill():
        cancelled = False
        if ctx.turn_running():
            ctx.current_turn.cancel()
            cancelled = True
        ctx.gate.confirmations.cancel_all()
        ctx.bus.publish("status", "ui", text="kill switch: current turn cancelled")
        return {"cancelled": cancelled}

    async def _pump(ws: WebSocket, kinds: set[str], ui_only: bool) -> None:
        """Forward filtered bus events to a websocket until it closes."""
        q = ctx.bus.subscribe()
        try:
            while True:
                event = await q.get()
                if event.kind not in kinds:
                    continue
                if ui_only and event.channel != "ui":
                    continue
                await ws.send_json(
                    {"type": event.kind, "ts": event.ts, "channel": event.channel, **event.payload}
                )
        finally:
            ctx.bus.unsubscribe(q)

    @app.websocket("/ws/chat")
    async def ws_chat(ws: WebSocket):
        await ws.accept()
        pump = asyncio.create_task(_pump(ws, _CHAT_KINDS, ui_only=True))
        try:
            while True:
                data = await ws.receive_json()
                if data.get("type") != "user_message":
                    continue
                text = str(data.get("text", "")).strip()
                if not text:
                    continue
                if ctx.turn_running():
                    await ws.send_json({"type": "busy"})
                    continue
                ctx.current_turn = asyncio.create_task(ctx.agent.run_turn(text))
        except WebSocketDisconnect:
            pass
        finally:
            pump.cancel()

    @app.websocket("/ws/activity")
    async def ws_activity(ws: WebSocket):
        await ws.accept()
        pump = asyncio.create_task(_pump(ws, _ACTIVITY_KINDS, ui_only=False))
        try:
            while True:
                await ws.receive_text()  # keepalive pings; content ignored
        except WebSocketDisconnect:
            pass
        finally:
            pump.cancel()

    return app


def _toast(title: str, message: str) -> None:
    try:
        from winotify import Notification

        Notification(app_id="Baby", title=title, msg=message).show()
    except Exception:  # noqa: BLE001 — a failed toast must never block startup
        pass


async def run_ui(config: dict) -> None:
    """Boot the full text stack: DB, model, tools, agent, UI server."""
    from clients.cli import build_gate
    from core.providers.ollama import OllamaProvider
    from core.readiness import ready_check
    from tools import apps, register_all
    from tools import files as files_tools
    from tools import web as web_tools

    daily = config["models"]["daily"]
    provider = OllamaProvider(
        model=daily["model"],
        temperature=daily.get("temperature", 0.7),
        keep_alive=daily.get("keep_alive", "24h"),
        num_ctx=daily.get("num_ctx", 8192),
    )
    db = Database("baby.db")
    await db.connect()

    ok, notes = await ready_check(provider, db)
    for note in notes:
        print(note)
    if not ok:
        _toast("Baby could not start", notes[-1][:120])
        await db.close()
        sys.exit(1)

    register_all()
    web_tools.configure(
        engine=config.get("search", {}).get("engine", "ddg"),
        max_results=config.get("search", {}).get("max_results", 6),
    )
    files_tools.configure(index_ttl_hours=config.get("files", {}).get("index_ttl_hours", 24))
    asyncio.get_running_loop().run_in_executor(None, apps.build_index)

    from memory import build_memory

    memory = await build_memory(config, db, provider)
    if memory is not None:
        print(f"memory ready ({await memory.store.count_active()} facts)")

    conv_id = await db.latest_conversation("ui") or await db.create_conversation("ui")
    bus = EventBus()
    gate = build_gate(config, bus)
    agent = AgentCore(
        provider,
        db,
        conv_id,
        channel="ui",
        bus=bus,
        gate=gate,
        memory=memory,
        suggest_next_step=config.get("persona", {}).get("suggest_next_step", True),
    )
    ctx = UIContext(db=db, bus=bus, gate=gate, agent=agent, config=config, memory=memory)
    app = create_app(ctx)

    ui_cfg = config.get("ui", {})
    host = ui_cfg.get("host", "127.0.0.1")
    port = int(ui_cfg.get("port", 8765))
    server = uvicorn.Server(
        uvicorn.Config(app, host=host, port=port, log_level="warning", access_log=False)
    )
    serve_task = asyncio.create_task(server.serve())
    while not server.started and not serve_task.done():
        await asyncio.sleep(0.05)
    if serve_task.done():
        _toast("Baby could not start", f"UI server failed to bind {host}:{port}")
        await db.close()
        sys.exit(1)

    ready_msg = f"Baby ready (text only) — http://{host}:{port}"
    print(ready_msg)
    _toast("Baby ready", f"UI at http://{host}:{port}")
    bus.publish("status", "ui", text=ready_msg)
    await db.add_audit("ui", "startup", "{}", "allow", 1, ready_msg)

    try:
        await serve_task
    finally:
        await db.close()
