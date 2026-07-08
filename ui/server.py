"""FastAPI web UI: chat + live activity feed, bound to 127.0.0.1 only.

The UI is a thin client: it renders the same event stream every other
surface consumes and submits messages to the same AgentCore.
"""

from __future__ import annotations

import asyncio
import logging
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

WEB_DIR = Path(__file__).parent / "web"  # vanilla UI (always at /classic)
APP_DIST = Path(__file__).parent / "app" / "dist"  # built v3 SPA (served at / when ui.frontend=v3)


class _NoCacheStatic(StaticFiles):
    """Serve app.js/style.css with no-store so a UI edit always lands on the
    next refresh — the browser used to hold a stale app.js (observed live: a
    fixed header badge kept rendering the old way until a hard refresh)."""

    def file_response(self, *args, **kwargs):
        resp = super().file_response(*args, **kwargs)
        resp.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
        return resp

_CHAT_KINDS = {"turn_start", "token", "turn_end", "error"}
_ACTIVITY_KINDS = {
    "tool_start",
    "tool_end",
    "confirm_request",
    "confirm_resolved",
    "status",
    "task_queued",
    "task_started",
    "task_done",
    "project_started",
    "project_done",
}


@dataclass
class UIContext:
    db: Database
    bus: EventBus
    gate: SafetyGate
    agent: AgentCore
    config: dict = field(default_factory=dict)
    memory: object | None = None  # memory.Memory; kept loose to avoid heavy import
    current_turn: asyncio.Task | None = None
    pool: object | None = None  # workers.queue.WorkerPool, attached in run_ui
    orchestrator: object | None = None  # workers.orchestrator.Orchestrator
    voice: object | None = None  # voice.pipeline.VoicePipeline (None when off)
    session_start: str = ""  # P5: SQLite-format ts marking this process's boot

    def turn_running(self) -> bool:
        if self.current_turn is not None and not self.current_turn.done():
            return True
        # Voice turns live in the pipeline, not in current_turn — /stats and
        # the busy check must see them too.
        voice_running = getattr(self.voice, "turn_running", None)
        return bool(voice_running and voice_running())


def create_app(ctx: UIContext) -> FastAPI:
    app = FastAPI(title="Baby", docs_url=None, redoc_url=None)
    # Vanilla UI assets — always mounted so /classic keeps working regardless of
    # the ui.frontend flag (config-first rollback for the whole v3 branch).
    app.mount("/static", _NoCacheStatic(directory=WEB_DIR), name="static")
    # v3 built assets (Vite emits absolute /assets/* refs). Present only after
    # `npm run build`; absent in a source checkout, which is fine — we fall back.
    if (APP_DIST / "assets").is_dir():
        app.mount("/assets", _NoCacheStatic(directory=APP_DIST / "assets"), name="assets")
    if ctx.config.get("ui", {}).get("frontend") == "v3" and not (APP_DIST / "index.html").is_file():
        logging.getLogger(__name__).warning(
            "ui.frontend=v3 but ui/app/dist is not built; serving classic UI. "
            "Build it with: npm --prefix ui/app ci && npm run build"
        )

    def _v3_ready() -> bool:
        frontend = ctx.config.get("ui", {}).get("frontend", "classic")
        return frontend == "v3" and (APP_DIST / "index.html").is_file()

    @app.get("/")
    async def index():
        # The flag picks the shell; /classic below is always reachable.
        if _v3_ready():
            return FileResponse(APP_DIST / "index.html")
        return FileResponse(WEB_DIR / "index.html")

    @app.get("/classic")
    async def classic():
        return FileResponse(WEB_DIR / "index.html")

    @app.get("/stats")
    async def stats():
        from tools.system_stats import snapshot

        data = await asyncio.to_thread(snapshot)
        data["model"] = ctx.config.get("models", {}).get("daily", {}).get("model", "?")
        data["turn_running"] = ctx.turn_running()
        router = getattr(ctx.agent.provider, "active", None)
        if router is not None:
            data["router"] = router
        counts = getattr(ctx.agent.provider, "turn_counts", None)
        if counts is not None:
            data["brain_turns"] = dict(counts)  # per-brain totals for the N4 soak
        game = getattr(ctx.agent.provider, "game_mode", None)
        if game is not None:
            data["game_mode"] = bool(game)
        latency = getattr(ctx.agent.provider, "latency", None)
        if latency:
            def pct(samples, p):
                ranked = sorted(samples)
                return round(ranked[min(len(ranked) - 1, int(p * len(ranked)))], 1)
            data["latency_ms"] = {
                tier: {"p50": pct(s, 0.5), "p95": pct(s, 0.95)}
                for tier, s in latency.items() if s
            }
        if ctx.pool is not None:
            data["tasks_running"] = ctx.pool.running_count()
        if ctx.orchestrator is not None:
            data["projects_running"] = ctx.orchestrator.running_count()
        screen_cfg = ctx.config.get("screen", {})
        if screen_cfg.get("enabled", True):
            data["vision"] = screen_cfg.get("model") or "daily multimodal"
        if ctx.voice is not None:
            verifier = getattr(ctx.voice, "verifier", None)
            data["speaker_verify"] = getattr(verifier, "note", "off") if verifier else "off"
        # P5 token telemetry: session (since boot) + today totals, per-brain split.
        since = ctx.session_start or await ctx.db.now()
        data["tokens"] = {
            "session": await ctx.db.usage_session(since),
            "today": await ctx.db.usage_today(),
        }
        return data

    @app.get("/tasks")
    async def tasks(limit: int = 50):
        return await ctx.db.list_tasks(limit)

    @app.get("/projects")
    async def projects(limit: int = 20):
        rows = await ctx.db.list_projects(limit)
        for row in rows:
            row["subtasks"] = [
                {k: t.get(k) for k in ("id", "title", "status", "result")}
                for t in await ctx.db.list_project_tasks(row["id"])
            ]
        return rows

    @app.get("/history")
    async def history(limit: int = 50):
        return await ctx.db.get_history(ctx.agent.conversation_id, limit)

    @app.post("/conversation/new")
    async def conversation_new():
        """Start a fresh UI conversation; the old one stays in the DB.

        A restart alone reuses the latest conversation by design (context
        survives reboots) — scored E2E battery runs need this to get a clean
        history, since the 9B's tool discipline drops on a polluted context.
        """
        if ctx.turn_running():
            return JSONResponse(
                {"error": "turn in progress — try again when idle"}, status_code=409
            )
        ctx.agent.conversation_id = await ctx.db.create_conversation("ui")
        ctx.bus.publish("status", "ui", text="fresh conversation started")
        return {"conversation_id": ctx.agent.conversation_id}

    @app.get("/memory")
    async def memory_view(limit: int = 200):
        if ctx.memory is None:
            return []
        return await ctx.memory.store.list_facts(limit)

    @app.delete("/memory/fact/{fact_id}")
    async def memory_delete_fact(fact_id: int):
        if ctx.memory is None:
            return JSONResponse({"error": "memory unavailable"}, status_code=404)
        result = await ctx.memory.store.delete_fact(fact_id)
        if "error" in result:
            return JSONResponse(result, status_code=404)
        return result

    @app.post("/memory/wipe")
    async def memory_wipe(body: dict):
        """Erase ALL memory. Challenge-gated: the body must carry phrase 'WIPE'
        (the browser modal makes the user type it), then the live UI session is
        flushed to a fresh conversation so nothing lingers until a restart."""
        if ctx.memory is None:
            return JSONResponse({"error": "memory unavailable"}, status_code=404)
        if str(body.get("phrase", "")).strip().upper() != "WIPE":
            return JSONResponse({"error": "type WIPE to confirm"}, status_code=400)
        counts = await ctx.memory.store.wipe_all()
        ctx.agent.conversation_id = await ctx.db.create_conversation("ui")
        ctx.agent.pending_suggestion = None
        await ctx.db.add_audit(
            "ui", "wipe_memory", "{}", "allow", 1,
            f"wiped {counts.get('facts', 0)} facts, {counts.get('messages', 0)} messages",
        )
        ctx.bus.publish("status", "ui", text="memory wiped")
        return counts

    @app.post("/confirm/{confirm_id}")
    async def confirm(confirm_id: str, body: dict):
        approved = bool(body.get("approved", False))
        if not ctx.gate.confirmations.resolve(confirm_id, approved):
            return JSONResponse({"error": "unknown or expired confirmation"}, status_code=404)
        return {"ok": True}

    @app.post("/kill")
    async def kill():
        cancelled = False
        if ctx.current_turn is not None and not ctx.current_turn.done():
            ctx.current_turn.cancel()
            cancelled = True
        cancel_voice = getattr(ctx.voice, "cancel_turn", None)
        if cancel_voice is not None:
            cancel_voice()  # voice turns are tracked in the pipeline
            cancelled = True
        ctx.gate.confirmations.cancel_all()
        ctx.bus.publish("status", "ui", text="kill switch: current turn cancelled")
        return {"cancelled": cancelled}

    @app.post("/game_mode")
    async def game_mode(body: dict):
        provider = ctx.agent.provider
        if not hasattr(provider, "set_game_mode"):
            return {"error": "game mode needs the cloud-primary router"}
        line = await provider.set_game_mode(bool(body.get("on", False)))
        return {"game_mode": provider.game_mode, "status": line}

    def _report_turn_crash(task: asyncio.Task) -> None:
        if task.cancelled():
            return  # kill switch — already reported as a cancelled turn
        exc = task.exception()
        if exc is not None:
            logging.getLogger("baby.ui").error("ui turn crashed", exc_info=exc)
            ctx.bus.publish("status", "ui", text=f"turn failed: {exc}")

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
                # Game-mode escape hatch: bare "game mode on/off" toggles
                # WITHOUT a model — in game mode with the cloud down there is
                # no brain left to call the tool (observed live deadlock).
                from tools.game import parse_game_command

                game = parse_game_command(text)
                if game is not None and hasattr(ctx.agent.provider, "set_game_mode"):
                    line = await ctx.agent.provider.set_game_mode(game)
                    await ws.send_json({"type": "turn_start"})
                    await ws.send_json({"type": "token", "text": line})
                    await ws.send_json(
                        {"type": "turn_end", "reply": line, "status": "ok", "brain": {}}
                    )
                    continue
                if ctx.turn_running():
                    await ws.send_json({"type": "busy"})
                    continue
                ctx.current_turn = asyncio.create_task(ctx.agent.run_turn(text))
                # Fire-and-forget task: without this, a crash mid-turn only
                # surfaces as "Task exception was never retrieved" on stderr
                # (observed live: a DB commit race). turn_end still arrives
                # (run_turn's finally), but the cause belongs in the log and
                # the activity feed.
                ctx.current_turn.add_done_callback(_report_turn_crash)
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


def _quiet_playwright_teardown(loop, context) -> None:
    """Ctrl+C on Windows hits the whole console group: the Playwright driver
    dies before our cleanup runs, and its orphaned reader future prints
    "Future exception was never retrieved ... Connection closed while reading
    from the driver" after "bye." (observed live). Harmless — silence exactly
    that; everything else goes to the default handler."""
    exc = context.get("exception")
    if exc is not None and "Connection closed while reading from the driver" in str(exc):
        return
    loop.default_exception_handler(context)


async def run_ui(config: dict, with_voice: bool = False) -> None:
    """Boot the full text stack: DB, model, tools, agent, UI server.

    with_voice=True additionally attaches the Phase 3 voice pipeline on its
    own thread; a voice failure degrades to text-only, never blocks boot.
    """
    from clients.cli import build_gate
    from core.readiness import ready_check
    from core.router import build_provider
    from tools import apps, register_all
    from tools import files as files_tools
    from tools import web as web_tools

    db = Database("baby.db")
    asyncio.get_running_loop().set_exception_handler(_quiet_playwright_teardown)
    await db.connect()
    bus = EventBus()
    provider = build_provider(config, bus=bus, db=db)

    wait_s = int(config.get("startup", {}).get("wait_for_model_s", 120))
    ok, notes = await ready_check(provider, db, wait_s=wait_s)
    for note in notes:
        print(note)
    if not ok:
        _toast("Baby could not start", notes[-1][:120])
        await db.close()
        sys.exit(1)

    # Cloud-primary router runs a 45 s NIM health probe; needs the live loop.
    if hasattr(provider, "start"):
        provider.start()

    gamewatch = None
    if config.get("game_mode", {}).get("auto_detect") and hasattr(provider, "set_game_mode"):
        from ui.gamewatch import GameWatch

        gamewatch = GameWatch(provider)
        gamewatch.start()

    register_all()
    web_tools.configure(
        engine=config.get("search", {}).get("engine", "ddg"),
        max_results=config.get("search", {}).get("max_results", 6),
    )
    files_tools.configure(index_ttl_hours=config.get("files", {}).get("index_ttl_hours", 24))
    from tools import browser as browser_tools

    browser_cfg = config.get("browser", {})
    browser_tools.configure(
        headless=bool(browser_cfg.get("headless", False)),
        profile_dir=browser_cfg.get("profile_dir", ""),
    )
    from core.vision import VisionService
    from tools import screen as screen_tools

    screen_tools.configure(VisionService(config, provider, bus))
    from tools import game as game_tools

    game_tools.configure(provider)
    asyncio.get_running_loop().run_in_executor(None, apps.build_index)

    from memory import build_memory

    memory = await build_memory(config, db, provider)
    if memory is not None:
        print(f"memory ready ({await memory.store.count_active()} facts)")

    conv_id = await db.latest_conversation("ui") or await db.create_conversation("ui")
    gate = build_gate(config, bus)
    # The per-domain browser confirm reads the REAL page url, never model kwargs.
    gate.session.browser_domain_fn = browser_tools.current_domain
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
    ctx.session_start = await db.now()  # P5: bound "session" token totals to boot
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

    voice_pipeline = None
    voice_ok = False
    voice_notes: list[str] = []
    if with_voice and config.get("voice", {}).get("enabled", True):
        from voice.pipeline import VoicePipeline

        voice_conv = await db.latest_conversation("voice") or await db.create_conversation("voice")
        voice_agent = AgentCore(
            provider,
            db,
            voice_conv,
            channel="voice",
            bus=bus,
            gate=gate,
            memory=memory,
            suggest_next_step=config.get("persona", {}).get("suggest_next_step", True),
        )
        voice_pipeline = VoicePipeline(
            asyncio.get_running_loop(), voice_agent, bus, config.get("voice", {})
        )
        voice_ok, voice_notes = await asyncio.to_thread(voice_pipeline.load)
        for note in voice_notes:
            print(f"voice: {note}")
        if voice_ok:
            voice_pipeline.start()
        else:
            voice_pipeline = None
        ctx.voice = voice_pipeline

    # Phase 4: notifications + background worker pool. Voice/telegram tiers
    # are injected as they come up; the pool works with toast-only too.
    from tools import tasks as tasks_tools
    from workers.notify import Notifier
    from workers.queue import WorkerPool

    notifier = Notifier(config, bus)
    notifier.voice = voice_pipeline  # None when voice is off/failed
    if hasattr(provider, "set_game_mode"):
        provider.notifier = notifier  # "Baby ready" announce after game-mode reload
    workers_cfg = config.get("workers", {})
    pool = WorkerPool(
        db=db,
        bus=bus,
        provider=provider,
        gate=gate,
        config=config,
        notifier=notifier,
        size=int(workers_cfg.get("size", 2)),
        max_iterations=int(workers_cfg.get("max_iterations", 15)),
    )
    pool.start()
    tasks_tools.configure(pool, db)
    ctx.pool = pool

    orchestrator = None
    if config.get("multi_agent", {}).get("enabled", True):
        from tools import projects as projects_tools
        from workers.orchestrator import Orchestrator

        orchestrator = Orchestrator(
            db=db, bus=bus, provider=provider, pool=pool, notifier=notifier, config=config
        )
        orchestrator.start()
        projects_tools.configure(orchestrator, db)
        ctx.orchestrator = orchestrator

    from workers.scheduler import Scheduler

    scheduler = Scheduler(
        db=db, bus=bus, provider=provider, gate=gate, config=config,
        notifier=notifier, memory=memory,
    )
    await scheduler.start()

    telegram_bot = None
    if config.get("telegram", {}).get("enabled", False):
        import os

        from clients.telegram_bot import TelegramBot

        token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
        chat_id = os.environ.get("TELEGRAM_CHAT_ID", "0")
        telegram_bot = TelegramBot(
            token=token,
            chat_id=int(chat_id or 0),
            db=db,
            bus=bus,
            gate=gate,
            provider=provider,
            config=config,
            memory=memory,
        )
        if await telegram_bot.start():
            notifier.telegram_send = telegram_bot.send_to_owner
            print("telegram ready (owner chat only)")
        else:
            telegram_bot = None
            print("telegram failed to start — continuing without it")

    tray = None
    if config.get("tray", {}).get("enabled", True):
        from ui.tray import TrayIcon

        loop = asyncio.get_running_loop()

        def _tray_quit() -> None:
            # Called on the pystray thread — hop to the loop to stop uvicorn.
            loop.call_soon_threadsafe(setattr, server, "should_exit", True)

        tray = TrayIcon(bus, url=f"http://{host}:{port}", on_quit=_tray_quit)
        if not tray.start():
            tray = None
            print("tray icon unavailable — continuing without it")

    if with_voice:
        from voice.readycue import ReadyCue

        cue = ReadyCue(config.get("voice", {}))
        if voice_ok:
            ready_msg = f"Baby ready — voice on ({voice_pipeline.wake.active_model}), UI at http://{host}:{port}"
            cue.play_full()
        else:
            ready_msg = f"Baby ready (text only) — voice failed to load, UI at http://{host}:{port}"
            cue.play_degraded()
    else:
        ready_msg = f"Baby ready (text only) — http://{host}:{port}"
    print(ready_msg)
    _toast("Baby ready", ready_msg)
    bus.publish("status", "ui", text=ready_msg)
    await db.add_audit("ui", "startup", "{}", "allow", 1, ready_msg)

    try:
        await serve_task
    finally:
        if tray is not None:
            try:
                tray.stop()
            except Exception:  # noqa: BLE001 — shutdown must reach every stage
                pass
        if telegram_bot is not None:
            try:
                await telegram_bot.stop()
            except Exception:  # noqa: BLE001 — shutdown must reach every stage
                pass
        try:
            await scheduler.stop()
        except Exception:  # noqa: BLE001
            pass
        if orchestrator is not None:
            try:
                await orchestrator.stop()
            except Exception:  # noqa: BLE001
                pass
        try:
            await pool.stop()
        except Exception:  # noqa: BLE001
            pass
        try:
            await browser_tools.shutdown()
        except Exception:  # noqa: BLE001
            pass
        if voice_pipeline is not None:
            voice_pipeline.stop()
        if gamewatch is not None:
            try:
                await gamewatch.stop()
            except Exception:  # noqa: BLE001
                pass
        if hasattr(provider, "stop"):
            try:
                await provider.stop()
            except Exception:  # noqa: BLE001
                pass
        await db.close()
