"""Phase N2: cloud-primary router — state machine, bucket, fallback, timeouts.

All offline: fake providers script every outcome; no network, no quota.
"""

from __future__ import annotations

import asyncio

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
    with pytest.raises(APIStatusError):
        await collect(router)
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
