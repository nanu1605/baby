"""Phase 4 stage 2: RouterProvider escalation logic (all offline)."""

from __future__ import annotations

import pytest

from core.bus import EventBus
from core.router import RouterProvider
from tests.conftest import FakeProvider

pytestmark = pytest.mark.asyncio

CFG = {
    "escalate_on": ["explicit_request", "planning_task", "retry_after_failure", "long_context"],
    "escalation_order": ["heavy", "cloud"],
    "long_context_tokens": 100,  # tiny so tests can trip it with short text
}
HEAVY_CFG = {"min_free_ram_gb": 22}


class Tier(FakeProvider):
    """FakeProvider with a controllable healthy() and a name."""

    def __init__(self, script, name="fake", is_healthy=True):
        super().__init__(script)
        self.name = name
        self.is_healthy = is_healthy

    async def healthy(self):
        return self.is_healthy


def make_router(
    *,
    daily=None,
    heavy=None,
    cloud=None,
    free_ram_gb=30.0,
    cfg=None,
    bus=None,
):
    router = RouterProvider(
        daily or Tier(["daily reply"], "daily"),
        heavy=heavy,
        cloud=cloud,
        bus=bus,
        db=None,
        router_cfg=cfg or CFG,
        heavy_cfg=HEAVY_CFG,
    )
    router._free_ram_gb = lambda: free_ram_gb
    return router


async def collect(router, text, *, tools=None, **opts):
    reply = ""
    async for chunk in router.chat([{"role": "user", "content": text}], tools=tools, **opts):
        reply += chunk.delta
    return reply


TOOLS = [{"type": "function", "function": {"name": "t"}}]


async def test_no_trigger_stays_daily():
    router = make_router(heavy=Tier(["heavy"], "heavy"), cloud=Tier(["cloud"], "cloud"))
    assert await collect(router, "what time is it") == "daily reply"
    assert router.active["tier"] == "daily"


async def test_explicit_request_goes_heavy_when_ram_high():
    heavy = Tier(["heavy reply"], "heavy")
    router = make_router(heavy=heavy, free_ram_gb=30)
    assert await collect(router, "use the big brain: refactor this") == "heavy reply"
    assert router.active["tier"] == "heavy"
    assert "explicit_request" in router.active["reason"]


async def test_ram_low_skips_heavy_to_cloud():
    heavy = Tier(["never"], "heavy")
    cloud = Tier(["cloud reply"], "cloud")
    router = make_router(heavy=heavy, cloud=cloud, free_ram_gb=8)
    assert await collect(router, "use the big brain please") == "cloud reply"
    assert heavy.requests == []
    assert router.active["tier"] == "cloud"


async def test_cloud_cooldown_falls_back_to_daily():
    cloud = Tier(["never"], "cloud", is_healthy=False)
    router = make_router(heavy=None, cloud=cloud, free_ram_gb=8)
    assert await collect(router, "use the big brain please") == "daily reply"
    assert "staying on daily" in router.active["reason"]


async def test_long_context_routes_to_cloud_never_heavy():
    heavy = Tier(["never"], "heavy")
    cloud = Tier(["cloud reply"], "cloud")
    router = make_router(heavy=heavy, cloud=cloud, free_ram_gb=30)
    long_text = "word " * 200  # 1000 chars ≈ 250 est. tokens > 100 threshold
    assert await collect(router, long_text) == "cloud reply"
    assert heavy.requests == []


async def test_planning_keyword_escalates():
    heavy = Tier(["heavy reply"], "heavy")
    router = make_router(heavy=heavy, free_ram_gb=30)
    assert await collect(router, "design a database schema for invoices") == "heavy reply"


async def test_disabled_trigger_is_ignored():
    heavy = Tier(["never"], "heavy")
    cfg = dict(CFG, escalate_on=["explicit_request"])
    router = make_router(heavy=heavy, free_ram_gb=30, cfg=cfg)
    assert await collect(router, "plan my week") == "daily reply"
    assert heavy.requests == []


async def test_internal_call_never_escalates():
    heavy = Tier(["never"], "heavy")
    router = make_router(heavy=heavy, free_ram_gb=30)
    # max_tokens + no tools = internal (summary/suggestion) call
    reply = await collect(router, "use the big brain to summarize", max_tokens=80)
    assert reply == "daily reply"
    assert heavy.requests == []


async def test_stickiness_within_turn_and_reset_across_turns():
    heavy = Tier(["heavy 1", "heavy 2"], "heavy")
    daily = Tier(["daily 1"], "daily")
    router = make_router(daily=daily, heavy=heavy, free_ram_gb=30)
    text = "use the big brain: plan the migration"
    # Same trailing user message twice = same turn (tool-loop iteration).
    assert await collect(router, text, tools=TOOLS) == "heavy 1"
    assert await collect(router, text, tools=TOOLS) == "heavy 2"
    assert "sticky" in router.active["reason"]
    # New user message = new turn → back to daily.
    assert await collect(router, "thanks!") == "daily 1"


async def test_retry_after_failure_arms_next_turn_only():
    heavy = Tier(["heavy reply"], "heavy")
    daily = Tier(["daily 1", "daily 2"], "daily")
    router = make_router(daily=daily, heavy=heavy, free_ram_gb=30)
    assert await collect(router, "first question") == "daily 1"
    router.record_turn_result("ui", "error")
    assert await collect(router, "second question") == "heavy reply"
    assert "retry_after_failure" in router.active["reason"]
    # Flag consumed — third turn back on daily.
    assert await collect(router, "third question") == "daily 2"


async def test_ok_turn_does_not_arm_retry():
    heavy = Tier(["never"], "heavy")
    router = make_router(heavy=heavy, free_ram_gb=30)
    router.record_turn_result("ui", "ok")
    assert await collect(router, "hello again") == "daily reply"
    assert heavy.requests == []


async def test_decision_and_denial_publish_status():
    bus = EventBus()
    q = bus.subscribe()
    heavy = Tier(["never"], "heavy")
    cloud = Tier(["cloud reply"], "cloud")
    router = make_router(heavy=heavy, cloud=cloud, free_ram_gb=8, bus=bus)
    await collect(router, "use the big brain please")
    texts = []
    while not q.empty():
        texts.append(q.get_nowait().payload.get("text", ""))
    assert any("heavy denied" in t for t in texts)
    assert any("cloud brain" in t for t in texts)


async def test_pre_first_chunk_failure_falls_back_to_daily():
    class Exploding(Tier):
        async def chat(self, messages, tools=None, **opts):
            raise RuntimeError("boom")
            yield  # pragma: no cover

    router = make_router(heavy=Exploding([], "heavy"), free_ram_gb=30)
    assert await collect(router, "use the big brain now") == "daily reply"


async def test_healthy_delegates_to_daily():
    daily = Tier([], "daily", is_healthy=False)
    router = make_router(daily=daily)
    assert await router.healthy() is False


# -- tier_hint (Phase 5 orchestrator override) ---------------------------------------


async def test_tier_hint_picks_heavy_when_ram_high():
    heavy = Tier(["heavy plan"], "heavy")
    router = make_router(heavy=heavy, free_ram_gb=30)
    reply = await collect(router, "plan-free text", tier_hint="best", max_tokens=1500)
    assert reply == "heavy plan"
    assert router.active["tier"] == "heavy"
    assert "tier_hint" in router.active["reason"]


async def test_tier_hint_falls_to_cloud_when_ram_low():
    heavy = Tier(["never"], "heavy")
    cloud = Tier(["cloud plan"], "cloud")
    router = make_router(heavy=heavy, cloud=cloud, free_ram_gb=8)
    reply = await collect(router, "anything", tier_hint="best", max_tokens=1500)
    assert reply == "cloud plan"
    assert router.active["tier"] == "cloud"


async def test_tier_hint_lands_daily_with_notice_when_nothing_available():
    heavy = Tier(["never"], "heavy")
    cloud = Tier(["never"], "cloud", is_healthy=False)
    router = make_router(heavy=heavy, cloud=cloud, free_ram_gb=8)
    reply = await collect(router, "anything", tier_hint="best", max_tokens=1500)
    assert reply == "daily reply"
    assert router.active["tier"] == "daily"
    assert "unavailable" in router.active["reason"]


async def test_tier_hint_beats_internal_call_short_circuit():
    # max_tokens set + tools None would normally force daily; the hint wins.
    heavy = Tier(["heavy"], "heavy")
    router = make_router(heavy=heavy, free_ram_gb=30)
    reply = await collect(router, "x", tier_hint="best", max_tokens=500)
    assert reply == "heavy"


async def test_tier_hint_ignores_sticky_cache():
    heavy = Tier(["heavy 1", "heavy 2"], "heavy")
    router = make_router(heavy=heavy, free_ram_gb=30)
    # Prime the sticky cache with a daily pick for this exact text.
    assert await collect(router, "same text") == "daily reply"
    assert router.active["tier"] == "daily"
    reply = await collect(router, "same text", tier_hint="best", max_tokens=100)
    assert reply == "heavy 1"


async def test_plain_internal_calls_still_daily():
    heavy = Tier(["never"], "heavy")
    router = make_router(heavy=heavy, free_ram_gb=30)
    reply = await collect(router, "use the big brain please", max_tokens=100)
    assert reply == "daily reply"
    assert router.active["reason"] == "internal call"


# -- P4 budget trim at the dispatch seam --------------------------------------


def _long_history(turns=10):
    msgs = [{"role": "system", "content": "S" * 40}]
    for i in range(turns):
        msgs.append({"role": "user", "content": f"old{i} " + "x" * 400})
        msgs.append({"role": "assistant", "content": f"reply{i} " + "y" * 400})
    msgs.append({"role": "user", "content": "the newest question"})
    return msgs


async def test_trim_applied_at_dispatch_seam():
    from core.context import estimate_tokens

    daily = Tier(["daily reply"], "daily")
    router = RouterProvider(
        daily, router_cfg=CFG, heavy_cfg=HEAVY_CFG,
        history_budgets={"daily": 30}, trim_enabled=True,
    )
    router._free_ram_gb = lambda: 30.0
    reply = ""
    async for chunk in router.chat(_long_history()):
        reply += chunk.delta
    sent = daily.requests[-1]
    assert sent[0]["role"] == "system"  # pinned
    assert any("the newest question" in (m.get("content") or "") for m in sent)  # newest kept
    hist = [m for m in sent if m["role"] != "system"]
    assert estimate_tokens(hist) <= 30  # trimmed to daily's budget


async def test_disabled_trim_sends_full_history():
    daily = Tier(["daily reply"], "daily")
    router = RouterProvider(
        daily, router_cfg=CFG, heavy_cfg=HEAVY_CFG,
        history_budgets={"daily": 30}, trim_enabled=False,  # engine v1
    )
    router._free_ram_gb = lambda: 30.0
    msgs = _long_history()
    async for _ in router.chat(msgs):
        pass
    assert len(daily.requests[-1]) == len(msgs)  # nothing dropped


async def test_fallback_retrims_to_new_brain_budget():
    from core.context import estimate_tokens

    class FailingTier(Tier):
        async def chat(self, messages, tools=None, **opts):
            self.requests.append([dict(m) for m in messages])
            raise RuntimeError("boom")
            yield  # pragma: no cover — unreachable, makes this an async gen

    cloud = FailingTier([], "cloud")
    daily = Tier(["daily reply"], "daily")
    router = RouterProvider(
        daily, cloud=cloud, router_cfg=CFG, heavy_cfg=HEAVY_CFG,
        history_budgets={"daily": 30, "cloud": 400}, trim_enabled=True,
    )
    router._free_ram_gb = lambda: 30.0
    msgs = _long_history()
    msgs[-1] = {"role": "user", "content": "use the big brain for this one"}  # → cloud
    reply = ""
    async for chunk in router.chat(msgs):
        reply += chunk.delta
    assert reply == "daily reply"  # cloud failed pre-stream, fell back to daily
    cloud_hist = [m for m in cloud.requests[-1] if m["role"] != "system"]
    daily_hist = [m for m in daily.requests[-1] if m["role"] != "system"]
    assert estimate_tokens(cloud_hist) <= 400  # each brain trimmed to ITS budget
    assert estimate_tokens(daily_hist) <= 30
    assert len(cloud_hist) > len(daily_hist)  # the roomier brain kept more turns
    assert len(cloud_hist) > len(daily_hist)  # the roomier brain kept more turns
