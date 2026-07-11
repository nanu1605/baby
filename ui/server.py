"""FastAPI web UI: chat + live activity feed, bound to 127.0.0.1 only.

The UI is a thin client: it renders the same event stream every other
surface consumes and submits messages to the same AgentCore.
"""

from __future__ import annotations

import asyncio
import logging
import os
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
    # V3e honest amplitude (additive) — the 3D core gauge's listening ripple /
    # speaking shimmer. Throttled ~15/~10 Hz at the source; intercepted client-side
    # by foldAmplitude BEFORE the event ring so they never pollute the feed.
    "mic_rms",
    "tts_rms",
}

# V2 frame governor: VRAM is pushed onto /ws/state so the client-side watchdog can
# demote 3D when the local model loads (spec §0.4 additive field). Both knobs below
# keep it cheap: sample pynvml at most every _VRAM_SAMPLE_S, and quantize so the
# exact-equality diff on the pump (_state_pump) does not fire on every wiggle.
_VRAM_SAMPLE_S = 1.5
_STATE_TICK_S = 1.5
_VRAM_BUCKET_GB = 0.25


def _quantize_vram(used_gb: float, bucket: float = _VRAM_BUCKET_GB) -> float:
    """Bucket VRAM to `bucket` GB so /ws/state's exact-equality diff stays quiet at
    idle and only fires when usage crosses a bucket (e.g. the local 9B loading)."""
    return round(round(used_gb / bucket) * bucket, 2)


_DEFAULT_RENDER = {"target_fps": 60, "tier": "auto", "idle_full_on_desktop": True}


def _render_config(config: dict) -> dict:
    """Additive render.* config for the V2 frame governor, code-defaulted. Read the
    same way as ui.frontend; never requires a config.yaml edit."""
    raw = config.get("render") if isinstance(config, dict) else None
    r = raw if isinstance(raw, dict) else {}

    def _as_int(v, default: int) -> int:
        # A malformed value (e.g. target_fps: "60fps") must degrade to the default,
        # never 500 the whole /stats payload.
        try:
            return int(v)
        except (TypeError, ValueError):
            return default

    return {
        "target_fps": _as_int(r.get("target_fps"), _DEFAULT_RENDER["target_fps"]),
        "tier": str(r.get("tier", _DEFAULT_RENDER["tier"])),
        "idle_full_on_desktop": bool(
            r.get("idle_full_on_desktop", _DEFAULT_RENDER["idle_full_on_desktop"])
        ),
    }


def _ui_brain(config: dict) -> str:
    """Additive read of ui.brain for the V3 3D-sphere gate. Code-default "3d" (the
    centerpiece; the frame governor auto-demotes to the 2D floor under pressure).
    "2d" is the one-line rollback to the v3 canvas graph. Never requires a config edit."""
    raw = config.get("ui", {}) if isinstance(config, dict) else {}
    val = str(raw.get("brain", "3d")).strip().lower() if isinstance(raw, dict) else "3d"
    return "2d" if val == "2d" else "3d"


class _StateDeriver:
    """Fold the bus event stream into a single pipeline state for the gauge (B1c).

    `thinking / speaking / executing` do not exist in the voice pipeline — it only
    has IDLE/LISTENING/RESPONDING. They are SYNTHESIZED here from what the agent
    actually emits: turn_start→thinking, first token→speaking, tool_start→
    executing, tool_end→executing (while tools stay open) else thinking,
    turn_end→idle; the voice "listening" status maps to listening.
    """

    _VOICE_IDLE = ("conversation ended", "heard nothing", "interrupted", "stopped")

    def __init__(self) -> None:
        self.state = "idle"
        self._open: set = set()

    def feed(self, event) -> str:
        kind = event.kind
        if kind == "turn_start":
            self.state = "thinking"
        elif kind == "token":
            self.state = "speaking"
        elif kind == "tool_start":
            self._open.add(event.payload.get("call_id"))
            self.state = "executing"
        elif kind == "tool_end":
            self._open.discard(event.payload.get("call_id"))
            self.state = "executing" if self._open else "thinking"
        elif kind == "turn_end":
            self._open.clear()
            self.state = "idle"
        elif kind == "status" and event.channel == "voice":
            text = str(event.payload.get("text", "")).lower()
            if "listening" in text:
                self.state = "listening"
            elif any(k in text for k in self._VOICE_IDLE):
                self.state = "idle"
        return self.state


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
    scheduler: object | None = None  # workers.scheduler.Scheduler, attached in run_ui
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

    @app.get("/api/graph")
    async def api_graph():
        # Topology of Baby's mind: subsystems + auto-derived tool/brain nodes.
        from core.nodes import build_graph

        return build_graph(ctx.config)

    @app.get("/api/nodes/{node_id}/stats")
    async def api_node_stats(node_id: str):
        # Live per-node stats for the inspector drawer. Read-only; dispatch by the
        # node-id prefix that core/nodes.py assigns.
        provider = ctx.agent.provider
        if node_id.startswith("tool:"):
            name = node_id.split(":", 1)[1]
            enabled = name not in await ctx.db.disabled_tools()
            return {
                "id": node_id, "type": "tool", "enabled": enabled,
                **(await ctx.db.audit_stats(name)),
            }
        if node_id.startswith("brain:"):
            tier = node_id.split(":", 1)[1]
            ranked = sorted((getattr(provider, "latency", None) or {}).get(tier) or [])

            def _pct(p):
                if not ranked:
                    return None
                return round(ranked[min(len(ranked) - 1, int(p * len(ranked)))], 1)

            active = getattr(provider, "active", None) or {}
            return {
                "id": node_id,
                "type": "brain",
                "latency_ms": {"p50": _pct(0.5), "p95": _pct(0.95)},
                "tokens": await ctx.db.usage_by_brain(tier),
                "turns": (getattr(provider, "turn_counts", None) or {}).get(tier, 0),
                "current": isinstance(active, dict) and active.get("tier") == tier,
                "router_state": active.get("state") if isinstance(active, dict) else None,
                "pinned_next_turn": ctx.agent._tier_hint_once == "best",
            }
        if node_id == "task_queue":
            running = ctx.pool.running_count() if ctx.pool is not None else 0
            return {
                "id": node_id,
                "type": "infra",
                "running": running,
                "queued": await ctx.db.count_tasks("queued"),
                "tasks": await ctx.db.list_tasks(limit=20),
            }
        if node_id == "scheduler":
            jobs = []
            if ctx.scheduler is not None:
                for j in ctx.scheduler.jobs():
                    jobs.append(
                        {"id": getattr(j, "id", None),
                         "next_run": str(getattr(j, "next_run_time", "") or "")}
                    )
            return {"id": node_id, "type": "infra", "jobs": jobs}
        if node_id in ("mem_facts", "mem_rag", "mem_summaries"):
            facts = await ctx.memory.store.count_active() if ctx.memory is not None else 0
            return {"id": node_id, "type": "memory", "facts": facts}
        # Subsystems without dedicated stats still return a valid, minimal payload
        # so the inspector never hits an empty/404 drawer.
        return {"id": node_id, "type": "subsystem"}

    @app.get("/api/search")
    async def api_search(q: str = ""):
        # "Search the brain": fan out over facts (vector), conversations (vector +
        # FTS), activity (audit FTS), tasks (FTS). Grouped by type — cosine and
        # bm25 scores aren't comparable, so there's no cross-type global rank.
        q = (q or "").strip()
        groups: dict[str, list] = {"facts": [], "conversations": [], "activity": [], "tasks": []}
        if not q:
            return {"query": q, "groups": groups}

        store = getattr(ctx.memory, "store", None) if ctx.memory is not None else None

        if store is not None:
            for f in await store.search(q, k=6, update_last_used=False):
                groups["facts"].append({
                    "type": "fact", "id": f["id"], "snippet": f["text"],
                    "ts": None, "node_id": "mem_facts",
                })

        seen: set[int] = set()
        if store is not None:
            for m in await store.search_messages(q, k=4):
                seen.add(m["id"])
                groups["conversations"].append({
                    "type": "conversation", "id": m["id"], "snippet": (m["text"] or "")[:200],
                    "ts": m.get("created_at"), "node_id": "mem_rag",
                    "conversation_id": m.get("conversation_id"),
                })
        for r in await ctx.db.search_messages_fts(q, limit=6):
            if r["id"] in seen:
                continue
            groups["conversations"].append({
                "type": "conversation", "id": r["id"], "snippet": (r["content"] or "")[:200],
                "ts": r["created_at"], "node_id": "mem_rag",
                "conversation_id": r["conversation_id"],
            })

        for r in await ctx.db.search_audit_fts(q, limit=8):
            summary = r["result_summary"] or ""
            groups["activity"].append({
                "type": "activity", "id": r["id"],
                "snippet": f'{r["tool"]}: {summary}'[:200],
                "ts": r["ts"], "node_id": f'tool:{r["tool"]}',
            })

        for r in await ctx.db.search_tasks_fts(q, limit=8):
            groups["tasks"].append({
                "type": "task", "id": r["id"], "snippet": r["title"],
                "ts": r["created_at"], "node_id": "task_queue", "status": r["status"],
            })

        return {"query": q, "groups": groups}

    @app.get("/stats")
    async def stats():
        from tools.system_stats import snapshot

        data = await asyncio.to_thread(snapshot)
        data["model"] = ctx.config.get("models", {}).get("daily", {}).get("model", "?")
        data["turn_running"] = ctx.turn_running()
        data["render"] = _render_config(ctx.config)  # V2 governor knobs (code-defaulted)
        data["ui"] = {"brain": _ui_brain(ctx.config)}  # V3 sphere gate (code-defaulted 3d)
        router = getattr(ctx.agent.provider, "active", None)
        if router is not None:
            data["router"] = router
        counts = getattr(ctx.agent.provider, "turn_counts", None)
        if counts is not None:
            data["brain_turns"] = dict(counts)  # per-brain totals for the N4 soak
        game = getattr(ctx.agent.provider, "game_mode", None)
        if game is not None:
            data["game_mode"] = bool(game)
        # V3 watchdog signal (additive §0.4 GPU-state): is the local model resident?
        # Read from the shared sampler cache; omitted while unknown (fail-open UI).
        _ensure_vram_sampler()
        if _vram_cache["local_loaded"] is not None:
            data["local_model_loaded"] = _vram_cache["local_loaded"]
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
            note = getattr(verifier, "note", "off") if verifier else "off"
            # B6: append the live session-trust tier + smoothed score when on.
            trust = getattr(ctx.voice, "session_trust", None)
            if trust is not None and getattr(verifier, "enabled", False):
                note += f" · trust {trust.tier}"
                if trust.smoothed is not None:
                    note += f" ({trust.smoothed:.2f})"
            data["speaker_verify"] = note
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

    @app.get("/api/conversations")
    async def api_conversations(
        limit: int = 50,
        offset: int = 0,
        include_archived: bool = False,
        channel: str = "ui",
    ):
        """History sidebar list (v5): real conversations with a derived title +
        message_count + last_message_at, newest activity first. Empty/unused and
        (by default) archived rows are excluded. active_conversation_id lets the
        sidebar highlight the live chat before any turn fires — the id is
        otherwise only on the turn_start WS payload."""
        convos = await ctx.db.list_conversations(
            channel=channel,
            limit=max(1, min(limit, 200)),
            offset=max(0, offset),
            include_archived=include_archived,
        )
        return {
            "conversations": convos,
            "active_conversation_id": ctx.agent.conversation_id,
        }

    @app.get("/api/conversations/{conv_id}")
    async def api_conversation_detail(conv_id: int, limit: int = 200):
        """One conversation's messages, read-only (v5 viewer). Reuses get_history
        (status='ok' + user/assistant only), so quarantined/failed turns never
        render. 404 when the conversation doesn't exist."""
        meta = await ctx.db.get_conversation_meta(conv_id)
        if meta is None:
            return JSONResponse({"error": "no such conversation"}, status_code=404)
        messages = await ctx.db.get_history(conv_id, max(1, min(limit, 500)))
        return {"meta": meta, "messages": messages}

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

    @app.post("/api/tools/{name}/flag")
    async def tool_flag(name: str, body: dict):
        # Enable/disable a tool → its schema is (un)hidden from the model next
        # turn. Reject any name that is not a real registered tool, so the safety
        # gate (not a tool) can never be "disabled" through this seam.
        from tools import registry

        if not registry.is_registered(name):
            return JSONResponse({"error": f"unknown tool: {name}"}, status_code=404)
        enabled = bool(body.get("enabled", True))
        await ctx.db.set_tool_flag(name, enabled)
        return {"name": name, "enabled": enabled}

    @app.post("/api/brain/boost")
    async def brain_boost(body: dict):
        # One-shot "prefer the strongest brain next turn" (tier_hint="best"). The
        # router keeps it subordinate to every privacy/health pin. Arming is
        # audited as an explicit user request.
        on = bool(body.get("on", False))
        ctx.agent._tier_hint_once = "best" if on else None
        if on:
            await ctx.db.add_audit(
                "ui", "brain_boost", "{}", "allow", 1, "explicit_request: boost armed"
            )
        return {"armed": on}

    @app.post("/api/tasks/{task_id}/cancel")
    async def task_cancel(task_id: int):
        if ctx.pool is None:
            return JSONResponse({"error": "task queue unavailable"}, status_code=404)
        cancelled = await ctx.pool.cancel(task_id)
        return {"cancelled": bool(cancelled)}

    @app.post("/api/scheduler/{job_id}/run")
    async def scheduler_run(job_id: str):
        if ctx.scheduler is None:
            return JSONResponse({"error": "scheduler unavailable"}, status_code=404)
        ran = await ctx.scheduler.run_now(job_id)
        if not ran:
            return JSONResponse({"error": f"unknown job: {job_id}"}, status_code=404)
        return {"ran": True}

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

    _vram_cache: dict = {"used": None, "total": None, "local_loaded": None, "task": None}

    def _read_gpu():
        try:
            from tools.system_stats import _gpu

            return _gpu()
        except Exception:  # noqa: BLE001 — no GPU is not an error
            return None

    async def _read_local_loaded():
        # V3 watchdog signal (additive §0.4): is the LOCAL daily model resident in
        # VRAM right now? Reuses the provider's own read-only /api/ps helper —
        # desktop-wide NVML "free" proved meaningless on Windows (apps overcommit),
        # so the honest #118 demote trigger is model residency, not headroom.
        # All provider shapes expose loaded_context_length at the TOP level (the
        # router classes delegate to their daily Ollama; the bare local_primary
        # rollback provider self-reports), so read it there — reaching through
        # .daily missed the rollback shape (review-caught).
        # Semantics: no helper on the provider → None (field omitted → UI fails
        # open); helper answered → bool. Note the helper itself folds "ollama
        # unreachable" into None==not-resident → False here; that reads as "lift
        # the cap" during a wedged-daemon window, which the frame governor
        # backstops — conservative enough, and usually simply true (daemon down
        # means nothing is resident).
        try:
            fn = getattr(ctx.agent.provider, "loaded_context_length", None)
            if fn is None:
                return None
            return (await fn()) is not None
        except Exception:  # noqa: BLE001 — unknown beats a crashed sampler
            return None

    async def _vram_sampler() -> None:
        # One shared, throttled sampler for all /ws/state clients. The pynvml read
        # runs OFF the event loop (like /stats' snapshot) so a wedged NVIDIA driver
        # can never stall the single loop; _state_snapshot only reads the cache.
        while True:
            gpu = await asyncio.to_thread(_read_gpu)
            if gpu:
                _vram_cache["used"] = _quantize_vram(gpu["vram_used_gb"])
                _vram_cache["total"] = gpu["vram_total_gb"]
            else:
                _vram_cache["used"] = None
                _vram_cache["total"] = None
            _vram_cache["local_loaded"] = await _read_local_loaded()
            await asyncio.sleep(_VRAM_SAMPLE_S)

    def _ensure_vram_sampler() -> None:
        if _vram_cache["task"] is None:
            _vram_cache["task"] = asyncio.ensure_future(_vram_sampler())

    def _state_snapshot(deriver: _StateDeriver) -> dict:
        # Pipeline state comes from the event stream (deriver); router health +
        # game mode are read live off the provider each time (they change without
        # a state transition — e.g. Wi-Fi drops → cloud→degraded).
        provider = ctx.agent.provider
        active = getattr(provider, "active", None) or {}
        router = active.get("state") if isinstance(active, dict) else None
        snap = {
            "type": "state",
            "state": deriver.state,
            "router": router or "unknown",
            "game_mode": bool(getattr(provider, "game_mode", False)),
        }
        used = _vram_cache["used"]
        if used is not None:
            snap["vram_used_gb"] = used
            snap["vram_total_gb"] = _vram_cache["total"]
        if _vram_cache["local_loaded"] is not None:
            snap["local_model_loaded"] = _vram_cache["local_loaded"]
        return snap

    async def _state_pump(ws: WebSocket) -> None:
        """Synthesize + push pipeline state; send only on change (plus an initial
        snapshot so a fresh client paints immediately). A periodic tick re-evaluates
        even without a bus event so a VRAM-bucket change still reaches the client
        (the governor's watchdog needs it while the pipeline is otherwise idle)."""
        _ensure_vram_sampler()  # shared, lazy-started on the first /ws/state client
        q = ctx.bus.subscribe()
        deriver = _StateDeriver()
        last = _state_snapshot(deriver)
        get_task = asyncio.ensure_future(q.get())
        try:
            await ws.send_json(last)
            while True:
                tick = asyncio.ensure_future(asyncio.sleep(_STATE_TICK_S))
                done, _ = await asyncio.wait(
                    {get_task, tick}, return_when=asyncio.FIRST_COMPLETED
                )
                tick.cancel()
                if get_task in done:
                    deriver.feed(get_task.result())
                    get_task = asyncio.ensure_future(q.get())  # re-arm; never lose an event
                snap = _state_snapshot(deriver)
                if snap != last:
                    await ws.send_json(snap)
                    last = snap
        finally:
            get_task.cancel()
            ctx.bus.unsubscribe(q)

    @app.websocket("/ws/state")
    async def ws_state(ws: WebSocket):
        await ws.accept()
        pump = asyncio.create_task(_state_pump(ws))
        try:
            while True:
                await ws.receive_text()  # keepalive pings; content ignored
        except WebSocketDisconnect:
            pass
        finally:
            pump.cancel()

    return app


def _shell_owns_tray(config: dict) -> bool:
    """The backend skips its own pystray icon when the v4 desktop shell owns the system
    tray, to avoid a double tray (V1 reconciliation). True when the shell spawned us —
    it sets BABY_SHELL_TRAY=1, so launching baby-shell.exe suppresses the backend tray
    even if ui.shell was never set — OR when ui.shell: native (the attached-service path,
    where the env var is absent). Additive read — code-defaults to browser/off; changes
    no product logic."""
    if os.environ.get("BABY_SHELL_TRAY") == "1":
        return True
    return str(config.get("ui", {}).get("shell", "browser")).strip().lower() == "native"


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
    ctx.scheduler = scheduler  # B1: expose upcoming jobs to /api/nodes/{id}/stats

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
    if config.get("tray", {}).get("enabled", True) and not _shell_owns_tray(config):
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
