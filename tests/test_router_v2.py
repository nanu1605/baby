"""Phase N2: cloud-primary router — state machine, bucket, fallback, timeouts.

All offline: fake providers script every outcome; no network, no quota.
"""

from __future__ import annotations

import asyncio
import json

import httpx
import pytest
from openai import APIStatusError

from core.providers.base import Chunk
from core.ratelimit import TokenBucket
from core.router import CloudRouter, HealthMonitor, RouterProvider, build_provider
from tests.conftest import FakeProvider

pytestmark = pytest.mark.asyncio


def status_error(code):
    request = httpx.Request("POST", "https://nim.test/v1")
    return APIStatusError("boom", response=httpx.Response(code, request=request), body=None)


class NimFake(FakeProvider):
    """FakeProvider with NIM extras: scripted failures, delay, probe."""

    name = "nvidia"
    model = "fake/nim"

    def __init__(self, script, *, fail=None, delay=0.0, probe_ok=True, is_healthy=True):
        super().__init__(script)
        self.fail = list(fail or [])  # exceptions raised pre-stream, in order
        self.delay = delay
        self.probe_ok = probe_ok
        self.is_healthy = is_healthy
        self.probe_calls: list[bool] = []

    async def chat(self, messages, tools=None, **opts):
        if self.fail:
            self.requests.append([dict(m) for m in messages])
            raise self.fail.pop(0)
        if self.delay:
            await asyncio.sleep(self.delay)
        async for chunk in super().chat(messages, tools=tools, **opts):
            yield chunk

    async def healthy(self):
        return self.is_healthy

    async def probe(self, generation=False):
        self.probe_calls.append(generation)
        return self.probe_ok


class MidStreamBomb(NimFake):
    """Yields one text chunk, then dies — mid-stream failure."""

    async def chat(self, messages, tools=None, **opts):
        self.requests.append([dict(m) for m in messages])
        yield Chunk(delta="partial ")
        raise status_error(502)


ROUTER_CFG = {
    "first_token_timeout_s": {"voice": 0.05, "text": 5},
    "health": {"probe_s": 45, "recover_after": 3, "cooldown_429_s": 90},
    "rate_limit": {"rpm": 36, "background_share": 0.5},
}


def make_router(*, daily=None, primary=None, heavy=None, backstop=None, cfg=None):
    return CloudRouter(
        daily or FakeProvider(["local reply"]),
        nim_primary=primary,
        nim_heavy=heavy,
        backstop=backstop,
        router_cfg=cfg or ROUTER_CFG,
    )


async def collect(router, text="hello", *, tools=None, **opts):
    reply = ""
    async for chunk in router.chat([{"role": "user", "content": text}], tools=tools, **opts):
        reply += chunk.delta
    return reply


# -- ladder ------------------------------------------------------------------------


async def test_normal_turn_routes_nim_primary():
    primary = NimFake(["cloud reply"])
    router = make_router(primary=primary, backstop=FakeProvider(["never"]))
    assert await collect(router) == "cloud reply"
    assert router.active["tier"] == "nim_primary"
    assert router.turn_counts["nim_primary"] == 1


async def test_tier_hint_and_planning_go_heavy():
    heavy = NimFake(["heavy reply", "heavy reply"])
    router = make_router(primary=NimFake(["never"]), heavy=heavy)
    assert await collect(router, tier_hint="best") == "heavy reply"
    assert await collect(router, "design a database schema") == "heavy reply"
    assert router.turn_counts["nim_heavy"] == 2


async def test_offline_state_routes_local_without_touching_cloud():
    primary = NimFake(["never"])
    router = make_router(primary=primary)
    router.monitor.state = "offline"
    assert await collect(router) == "local reply"
    assert primary.requests == []
    assert router.active["tier"] == "daily"


async def test_internal_capped_call_stays_local():
    primary = NimFake(["never"])
    router = make_router(primary=primary)
    assert await collect(router, max_tokens=80) == "local reply"
    assert primary.requests == []


async def test_orchestrator_call_beats_internal_short_circuit():
    # Planning/integration calls are capped AND toolless — tier_hint="best"
    # must still reach the heavy brain (regression: legacy _pick order).
    heavy = NimFake(["plan json"])
    router = make_router(primary=NimFake(["never"]), heavy=heavy)
    reply = await collect(router, tier_hint="best", max_tokens=1500)
    assert reply == "plan json"


async def test_planning_word_inside_internal_call_stays_local():
    heavy = NimFake(["never"])
    router = make_router(primary=NimFake(["never"]), heavy=heavy)
    assert await collect(router, "summarize: we plan a trip", max_tokens=80) == "local reply"
    assert heavy.requests == []


async def test_pin_local_opt_routes_daily():
    primary = NimFake(["never"])
    router = make_router(primary=primary)
    assert await collect(router, pin_local=True, pin_reason="privacy pin") == "local reply"
    assert primary.requests == []
    assert router.active["reason"] == "privacy pin"


async def test_bucket_empty_overflows_to_local_skipping_backstop():
    primary = NimFake(["never"])
    backstop = FakeProvider(["never"])
    router = make_router(primary=primary, backstop=backstop)
    router.bucket = TokenBucket(rpm=1)
    assert router.bucket.try_acquire()  # exhaust the window
    assert await collect(router) == "local reply"
    assert primary.requests == []
    assert backstop.requests == []  # overflow skips the cloud ENTIRELY


# -- fallback ----------------------------------------------------------------------


async def test_prestream_failure_falls_to_backstop_with_identical_messages():
    primary = NimFake([], fail=[status_error(500)])
    backstop = FakeProvider(["backstop reply"])
    router = make_router(primary=primary, backstop=backstop)
    messages = [{"role": "user", "content": "hi"},]
    reply = ""
    async for chunk in router.chat(list(messages), tools=None):
        reply += chunk.delta
    assert reply == "backstop reply"
    assert backstop.requests[0] == primary.requests[0] == messages
    assert router.monitor.state == "degraded"


async def test_429_sets_cooldown_and_next_call_skips_nim():
    primary = NimFake(["later"], fail=[status_error(429)])
    backstop = FakeProvider(["backstop reply", "backstop reply"])
    router = make_router(primary=primary, backstop=backstop)
    assert await collect(router) == "backstop reply"
    assert router.monitor.cooling_down()
    assert await collect(router) == "backstop reply"
    assert len(primary.requests) == 1  # second turn never touched NIM


async def test_voice_timeout_drops_rung_text_does_not():
    primary = NimFake(["slow reply", "slow reply"], delay=0.15)
    router = make_router(primary=primary)
    assert await collect(router, channel="voice") == "local reply"
    assert router.monitor.state == "degraded"
    router.monitor.state = "cloud"
    assert await collect(router, channel="ui") == "slow reply"


async def test_midstream_failure_surfaces_not_restarts():
    router = make_router(primary=MidStreamBomb([]), backstop=FakeProvider(["never"]))
    with pytest.raises(APIStatusError):
        await collect(router)


async def test_all_rungs_dead_raises_last_error():
    primary = NimFake([], fail=[status_error(500)])
    router = make_router(daily=None, primary=primary)
    router.daily = None  # simulate no local at all
    with pytest.raises(APIStatusError):
        await collect(router)


# -- game mode ----------------------------------------------------------------------


async def test_game_mode_never_touches_local():
    daily = FakeProvider(["never"])
    primary = NimFake(["cloud reply"])
    router = make_router(daily=daily, primary=primary)
    router.game_mode = True
    assert await collect(router) == "cloud reply"
    assert daily.requests == []


async def test_game_mode_offline_is_honest_failure():
    daily = FakeProvider(["never"])
    primary = NimFake([], fail=[status_error(500)])
    router = make_router(daily=daily, primary=primary)
    router.game_mode = True
    reply = await collect(router)
    assert "game mode off" in reply.lower()  # spoken way out, never dead air
    assert daily.requests == []


# -- health monitor -----------------------------------------------------------------


async def test_failure_reasons_map_to_states():
    monitor = HealthMonitor(None)
    monitor.note_failure("timeout")
    assert monitor.state == "degraded"
    monitor.note_failure("dns_fail")
    assert monitor.state == "offline"
    monitor.note_failure("429")  # a 429 report never upgrades offline
    assert monitor.state == "offline" and monitor.cooling_down()


async def test_hysteresis_needs_three_probes_and_gen_ping():
    nim = NimFake([], probe_ok=True)
    monitor = HealthMonitor(nim, cfg={"recover_after": 3})
    monitor.state = "degraded"
    await monitor._probe_once()
    await monitor._probe_once()
    assert monitor.state == "degraded"  # 2 good probes: no flap
    await monitor._probe_once()
    assert monitor.state == "cloud"
    assert nim.probe_calls[-1] is True  # final hop proved generation


async def test_gen_ping_failure_blocks_recovery():
    class GenFails(NimFake):
        async def probe(self, generation=False):
            self.probe_calls.append(generation)
            return not generation

    nim = GenFails([])
    monitor = HealthMonitor(nim, cfg={"recover_after": 2})
    monitor.state = "degraded"
    await monitor._probe_once()
    await monitor._probe_once()
    assert monitor.state == "degraded"  # connectivity ok, generation dead


async def test_offline_probe_recovers_to_degraded_first():
    nim = NimFake([], probe_ok=True)
    monitor = HealthMonitor(nim, cfg={"recover_after": 3})
    monitor.state = "offline"
    await monitor._probe_once()
    assert monitor.state == "degraded"


async def test_live_successes_count_toward_recovery():
    monitor = HealthMonitor(None, cfg={"recover_after": 2})
    monitor.state = "degraded"
    monitor.note_success()
    assert monitor.state == "degraded"
    monitor.note_success()
    assert monitor.state == "cloud"


# -- build_provider modes -------------------------------------------------------------


LEGACY_CONFIG = {
    "models": {"daily": {"model": "qwen3.5:9b-q4_K_M"},
               "heavy": {"model": "qwen3.6:35b-a3b"}},
    "router": {"mode": "local_primary"},
}

CLOUD_CONFIG = {
    "models": {
        "daily": {"model": "qwen3.5:9b-q4_K_M"},
        "nim_primary": {"model": "minimaxai/minimax-m2.7"},
        "nim_heavy": {"model": "z-ai/glm-5.2"},
    },
    "router": {"mode": "cloud_primary"},
}


async def test_local_primary_mode_builds_legacy_router(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    provider = build_provider(LEGACY_CONFIG)
    assert isinstance(provider, RouterProvider)


async def test_cloud_primary_mode_builds_cloud_router(monkeypatch):
    monkeypatch.setenv("NVIDIA_API_KEY", "nvapi-test")
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    provider = build_provider(CLOUD_CONFIG)
    assert isinstance(provider, CloudRouter)
    assert provider.nim_primary.model == "minimaxai/minimax-m2.7"
    assert provider.nim_heavy.model == "z-ai/glm-5.2"


async def test_cloud_primary_without_key_fails_loud(monkeypatch):
    monkeypatch.delenv("NVIDIA_API_KEY", raising=False)
    with pytest.raises(ValueError, match="cloud_primary"):
        build_provider(CLOUD_CONFIG)


# -- degraded state must STAY fallen (observed live: 3.5s tax per tool step) ---------


async def test_degraded_skips_nim_and_serves_backstop():
    primary = NimFake(["never"])
    backstop = FakeProvider(["backstop reply"])
    router = make_router(primary=primary, backstop=backstop)
    router.monitor.state = "degraded"
    assert await collect(router) == "backstop reply"
    assert primary.requests == []  # no per-turn NIM gamble while degraded


async def test_degraded_internal_call_stays_daily():
    primary = NimFake(["never"])
    backstop = FakeProvider(["never"])
    router = make_router(primary=primary, backstop=backstop)
    router.monitor.state = "degraded"
    assert await collect(router, max_tokens=80) == "local reply"
    assert backstop.requests == []


async def test_slow_gen_ping_blocks_recovery():
    class SlowPing(NimFake):
        async def probe(self, generation=False):
            self.probe_calls.append(generation)
            if generation:
                await asyncio.sleep(0.2)  # past the budget below
            return True

    nim = SlowPing([])
    monitor = HealthMonitor(nim, cfg={"recover_after": 1, "gen_ping_timeout_s": 0.05})
    monitor.state = "degraded"
    await monitor._probe_once()
    assert monitor.state == "degraded"  # congested-but-alive is not recovered


# -- N3: privacy pins ------------------------------------------------------------------


def _pinned_messages(tool_name="read_file", content="SECRET-BYTES-42"):
    return [
        {"role": "user", "content": "read my notes"},
        {"role": "assistant", "content": None, "tool_calls": [
            {"id": "c1", "type": "function",
             "function": {"name": tool_name, "arguments": "{\"path\": \"notes.txt\"}"}}]},
        {"role": "tool", "tool_call_id": "c1", "content": content},
    ]


async def test_pinned_tool_result_forces_local():
    primary = NimFake(["never"])
    backstop = FakeProvider(["never"])
    router = make_router(primary=primary, backstop=backstop)
    reply = ""
    async for chunk in router.chat(_pinned_messages(), tools=None):
        reply += chunk.delta
    assert reply == "local reply"
    assert primary.requests == [] and backstop.requests == []
    assert "privacy pin (read_file)" in router.active["reason"]


async def test_privacy_pin_beats_game_mode():
    # Pinned bytes never leave the PC even when the local brain is unloaded.
    primary = NimFake(["never"])
    router = make_router(primary=primary)
    router.game_mode = True
    reply = ""
    async for chunk in router.chat(_pinned_messages(), tools=None):
        reply += chunk.delta
    assert reply == "local reply"
    assert primary.requests == []


async def test_game_mode_pinned_serve_evicts_immediately():
    # A pin-forced local serve during game mode must not leave the 9B camped
    # in VRAM under the configured 24h keep_alive (observed live: one
    # privacy-pin turn reloaded it for the rest of the game session) —
    # the router overrides keep_alive to 0 for that call.
    class OptsCapture(FakeProvider):
        def __init__(self, script):
            super().__init__(script)
            self.chat_opts: list[dict] = []

        async def chat(self, messages, tools=None, **opts):
            self.chat_opts.append(dict(opts))
            async for chunk in super().chat(messages, tools=tools, **opts):
                yield chunk

    daily = OptsCapture(["local reply", "local reply"])
    router = make_router(daily=daily, primary=NimFake(["never"]))
    router.game_mode = True
    async for _ in router.chat(_pinned_messages(), tools=None):
        pass
    assert daily.chat_opts[-1]["keep_alive"] == 0

    # Outside game mode the configured keep_alive stays untouched.
    router.game_mode = False
    async for _ in router.chat(_pinned_messages(), tools=None):
        pass
    assert "keep_alive" not in daily.chat_opts[-1]


async def test_redaction_masks_pinned_bytes_only():
    router = make_router(primary=NimFake(["x"]))
    messages = _pinned_messages() + [
        {"role": "assistant", "content": None, "tool_calls": [
            {"id": "c2", "type": "function",
             "function": {"name": "web_search", "arguments": "{}"}}]},
        {"role": "tool", "tool_call_id": "c2", "content": "public result"},
    ]
    out = router._redact_pinned(messages)
    tool_msgs = [m for m in out if m["role"] == "tool"]
    assert "SECRET-BYTES-42" not in str(out)
    assert "[local-only content redacted: read_file result, 15 bytes]" in tool_msgs[0]["content"]
    assert tool_msgs[1]["content"] == "public result"  # unpinned untouched


async def test_unpinned_tool_results_do_not_pin():
    primary = NimFake(["cloud reply"])
    router = make_router(primary=primary)
    reply = ""
    async for chunk in router.chat(_pinned_messages(tool_name="web_search"), tools=None):
        reply += chunk.delta
    assert reply == "cloud reply"


# -- N3: language pin -------------------------------------------------------------------


async def test_devanagari_message_pins_local():
    primary = NimFake(["never"])
    router = make_router(primary=primary)
    reply = await collect(router, "मेरे लिए कल का मौसम बताओ")
    assert reply == "local reply"
    assert primary.requests == []
    assert "language pin" in router.active["reason"]


async def test_roman_hinglish_is_not_pinned():
    primary = NimFake(["cloud reply"])
    router = make_router(primary=primary)
    assert await collect(router, "yaar kal ka weather kya hai") == "cloud reply"


# -- N3: game mode toggle -----------------------------------------------------------------


class UnloadableDaily(FakeProvider):
    def __init__(self, script):
        super().__init__(script)
        self.unloads = 0
        self.warms = 0

    async def unload(self):
        self.unloads += 1

    async def warm(self):
        self.warms += 1


async def test_set_game_mode_unloads_and_rewarns():
    daily = UnloadableDaily(["local reply"])
    router = make_router(daily=daily, primary=NimFake(["cloud reply"]))

    class FakeNotifier:
        def __init__(self):
            self.announced = []

        async def announce(self, text, **kw):
            self.announced.append(text)

    router.notifier = FakeNotifier()
    line = await router.set_game_mode(True)
    assert router.game_mode and daily.unloads == 1 and "ON" in line
    assert "already" in await router.set_game_mode(True)
    line = await router.set_game_mode(False)
    assert not router.game_mode and "OFF" in line
    await router._warm_task
    assert daily.warms == 1
    assert any("ready" in t.lower() for t in router.notifier.announced)


async def test_set_game_mode_tool_and_safety():
    from core.safety import SafetyConfig, classify_tool
    from tools import game as game_tools

    verdict = classify_tool("set_game_mode", {"on": True}, SafetyConfig())
    assert verdict.klass.value == "allow"

    router = make_router(daily=UnloadableDaily(["x"]), primary=NimFake(["y"]))
    game_tools.configure(router)
    import json as _json

    result = _json.loads(await game_tools.set_game_mode(True))
    assert result["game_mode"] is True
    game_tools.configure(None)
    result = _json.loads(await game_tools.set_game_mode(True))
    assert "error" in result


async def test_gamewatch_rect_logic():
    from ui.gamewatch import covers_monitor

    assert covers_monitor((0, 0, 2560, 1440), (0, 0, 2560, 1440))
    assert covers_monitor((-8, -8, 2568, 1448), (0, 0, 2560, 1440))  # borderless overhang
    assert not covers_monitor((0, 0, 1280, 720), (0, 0, 2560, 1440))  # windowed


# -- N4: brain surfacing + served audit ---------------------------------------------


class AuditDb:
    def __init__(self):
        self.rows = []

    async def add_audit(self, channel, tool, args, safety_class, approved, detail):
        self.rows.append((json.loads(args)["action"], detail))


async def test_active_carries_model_and_served_is_audited():
    import asyncio as _asyncio

    db = AuditDb()
    primary = NimFake(["cloud reply"])
    router = CloudRouter(
        FakeProvider(["local"]), nim_primary=primary, db=db, router_cfg=ROUTER_CFG
    )
    assert await collect(router, channel="ui") == "cloud reply"
    assert router.active["model"] == "fake/nim"
    await _asyncio.sleep(0)  # let the fire-and-forget audit tasks run
    served = [r for r in db.rows if r[0] == "served nim_primary"]
    assert len(served) == 1
    detail = json.loads(served[0][1])
    assert detail["channel"] == "ui" and detail["first_token_ms"] >= 0
    assert router.latency["nim_primary"]


# -- game-mode deadlock fixes (observed live: cloud down + local unloaded) -----------


async def test_game_mode_exhaustion_speaks_instead_of_dying():
    primary = NimFake([], fail=[status_error(500)])
    router = make_router(daily=FakeProvider(["never"]), primary=primary)
    router.game_mode = True
    reply = await collect(router)
    assert "game mode off" in reply.lower()  # honest way out, no dead air


async def test_parse_game_command():
    from tools.game import parse_game_command

    assert parse_game_command("game mode on") is True
    assert parse_game_command("Game Mode Off") is False
    assert parse_game_command("baby, gaming mode on!") is True
    assert parse_game_command("what is game mode?") is None
    assert parse_game_command("turn on the lights") is None


async def test_redaction_guards_even_a_bugged_ladder():
    # Defense-in-depth proof: if a FUTURE ladder bug routes pinned content to
    # a cloud rung, the payload that reaches the provider is already masked.
    primary = NimFake(["cloud reply"])
    router = make_router(primary=primary)
    router._ladder = lambda m, t, o: (["nim_primary"], "simulated ladder bug")
    reply = ""
    async for chunk in router.chat(_pinned_messages(), tools=None):
        reply += chunk.delta
    assert reply == "cloud reply"
    sent = str(primary.requests[0])
    assert "SECRET-BYTES-42" not in sent
    assert "local-only content redacted" in sent


async def test_language_pin_outranks_degraded_state():
    # Caught live by the E2E battery: Devanagari during DEGRADED was routed
    # to the Gemini backstop instead of the local Qwen.
    primary = NimFake(["never"])
    backstop = FakeProvider(["never"])
    router = make_router(primary=primary, backstop=backstop)
    router.monitor.state = "degraded"
    assert await collect(router, "आज का मौसम कैसा है?") == "local reply"
    assert backstop.requests == []
    assert "language pin" in router.active["reason"]


async def test_midstream_stall_raises_instead_of_hanging():
    # Observed live: a stream that stalled after its first chunk hung the
    # turn forever (turn_running stuck, every later message swallowed).
    class Staller(NimFake):
        async def chat(self, messages, tools=None, **opts):
            self.requests.append([dict(m) for m in messages])
            yield Chunk(delta="first ")
            await asyncio.sleep(5)  # far past the stall budget below
            yield Chunk(delta="never")

    cfg = dict(ROUTER_CFG, stall_timeout_s=0.1)
    router = make_router(primary=Staller([]), cfg=cfg)
    with pytest.raises(RuntimeError, match="stalled"):
        await collect(router)
    assert router.monitor.state == "degraded"
