"""Model router: daily → heavy → cloud escalation as a ChatProvider.

RouterProvider wraps the three brains behind the same protocol AgentCore
already consumes, so every surface (UI, voice, tasks, scheduler, telegram)
inherits routing with zero agent changes. Escalation triggers (spec §9.3):

  explicit_request     "use the big brain" / "cloud pe pooch" in the message
  planning_task        plan/design/architect keywords in the message
  retry_after_failure  the previous turn ended error/capped
  long_context         input over router.long_context_tokens — CLOUD only,
                       because OLLAMA_CONTEXT_LENGTH is global: heavy gets the
                       same 8K window as daily and can't fit it any better.

Heavy additionally needs psutil free RAM above models.heavy.min_free_ram_gb
(the 35B MoE lives mostly in system RAM). Every escalation AND every denial
is audited and surfaced on the bus. Internal capped calls (summary,
extraction, next-step: max_tokens set, no tools) always stay on daily.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections import OrderedDict
from collections.abc import AsyncIterator

from core.providers.base import ChatProvider, Chunk

_PLANNING_RE = re.compile(r"\b(plan|design|architect|blueprint|roadmap|strategy)\b", re.IGNORECASE)
_DEFAULT_EXPLICIT = (
    "use the big brain",
    "big brain",
    "use the heavy model",
    "think harder",
    "cloud pe pooch",
    "use the cloud",
    "ask the cloud",
)
_CLOUD_HINT_RE = re.compile(r"\bcloud\b", re.IGNORECASE)


def _trailing_user_text(messages: list[dict]) -> str:
    for message in reversed(messages):
        if message.get("role") == "user":
            return str(message.get("content") or "")
    return ""


def _estimate_tokens(messages: list[dict]) -> int:
    total = sum(len(str(m.get("content") or "")) for m in messages)
    return total // 4  # rough chars→tokens; only feeds a threshold


class RouterProvider:
    """ChatProvider that picks a brain per turn and falls back on failure."""

    name = "router"

    def __init__(
        self,
        daily: ChatProvider,
        *,
        heavy: ChatProvider | None = None,
        cloud: ChatProvider | None = None,
        bus=None,
        db=None,
        router_cfg: dict | None = None,
        heavy_cfg: dict | None = None,
    ) -> None:
        router_cfg = router_cfg or {}
        self.daily = daily
        self.heavy = heavy
        self.cloud = cloud
        self.bus = bus
        self.db = db
        self.escalate_on = set(
            router_cfg.get(
                "escalate_on",
                ["explicit_request", "planning_task", "retry_after_failure", "long_context"],
            )
        )
        self.escalation_order = list(router_cfg.get("escalation_order", ["heavy", "cloud"]))
        self.long_context_tokens = int(router_cfg.get("long_context_tokens", 4096))
        self.explicit_phrases = tuple(
            p.lower() for p in router_cfg.get("explicit_phrases", _DEFAULT_EXPLICIT)
        )
        self.min_free_ram_gb = float((heavy_cfg or {}).get("min_free_ram_gb", 22))
        self._retry_armed = False
        self._sticky: OrderedDict[str, str] = OrderedDict()  # turn key → tier
        self._active = {"tier": "daily", "reason": "default"}

    # -- AgentCore hook (duck-typed; see core/agent.py run_turn finally) --------

    def record_turn_result(self, channel: str, status: str) -> None:
        if "retry_after_failure" in self.escalate_on and status in ("error", "capped"):
            self._retry_armed = True

    @property
    def active(self) -> dict:
        """Last routing decision — surfaced as the /stats model badge."""
        return dict(self._active)

    # -- trigger detection --------------------------------------------------------

    def _triggers(self, messages: list[dict]) -> tuple[list[str], bool]:
        """(fired trigger names, cloud_only) for the trailing user message."""
        text = _trailing_user_text(messages).lower()
        fired: list[str] = []
        cloud_only = False
        if "explicit_request" in self.escalate_on and any(
            p in text for p in self.explicit_phrases
        ):
            fired.append("explicit_request")
            if _CLOUD_HINT_RE.search(text):
                cloud_only = True
        if "planning_task" in self.escalate_on and _PLANNING_RE.search(text):
            fired.append("planning_task")
        if "retry_after_failure" in self.escalate_on and self._retry_armed:
            fired.append("retry_after_failure")
        if (
            "long_context" in self.escalate_on
            and _estimate_tokens(messages) > self.long_context_tokens
        ):
            fired.append("long_context")
            cloud_only = True  # heavy shares the global 8K ctx — no gain
        return fired, cloud_only

    def _free_ram_gb(self) -> float:
        import psutil

        return psutil.virtual_memory().available / 2**30

    def _provider_for(self, tier: str) -> ChatProvider | None:
        return {"daily": self.daily, "heavy": self.heavy, "cloud": self.cloud}.get(tier)

    async def _first_available(self, candidates: list[str], why: str) -> tuple[str, str]:
        """First candidate that is configured, RAM-eligible and healthy; else daily."""
        for candidate in candidates:
            provider = self._provider_for(candidate)
            if provider is None:
                self._note_denial(candidate, f"{why}: {candidate} not configured")
                continue
            if candidate == "heavy":
                free = self._free_ram_gb()
                if free <= self.min_free_ram_gb:
                    self._note_denial(
                        "heavy", f"{why}: heavy denied — {free:.1f} GB free < "
                        f"{self.min_free_ram_gb:g} GB needed"
                    )
                    continue
            if not await provider.healthy():
                self._note_denial(candidate, f"{why}: {candidate} unhealthy/cooldown")
                continue
            return candidate, why
        return "daily", f"{why}: all escalation targets unavailable — staying on daily"

    async def _pick(self, messages: list[dict], tools, opts) -> tuple[str, str]:
        """(tier, reason). Consumes the retry flag; honors per-turn stickiness."""
        # Orchestrator override (Phase 5): tier_hint="best" asks for the best
        # available brain regardless of message triggers. Checked BEFORE the
        # internal-call short-circuit (planning/integration are capped no-tools
        # calls by design) and before stickiness (hinted calls are explicit).
        if opts.get("tier_hint") == "best":
            return await self._first_available(self.escalation_order, "tier_hint")

        # Internal capped calls (summary/extraction/suggestion) stay on daily.
        if opts.get("max_tokens") and not tools:
            return "daily", "internal call"

        key = hashlib.md5(_trailing_user_text(messages).encode("utf-8")).hexdigest()
        if key in self._sticky:
            self._sticky.move_to_end(key)
            return self._sticky[key], "sticky (same turn)"

        fired, cloud_only = self._triggers(messages)
        self._retry_armed = False
        tier, reason = "daily", "no trigger"
        if fired:
            why = "+".join(fired)
            candidates = ["cloud"] if cloud_only else self.escalation_order
            tier, reason = await self._first_available(candidates, why)

        self._sticky[key] = tier
        while len(self._sticky) > 8:
            self._sticky.popitem(last=False)
        return tier, reason

    # -- decision logging ---------------------------------------------------------

    def _note_denial(self, tier: str, detail: str) -> None:
        if self.bus is not None:
            self.bus.publish("status", "router", text=f"router: {detail}")
        self._audit(f"denied {tier}", detail)

    def _note_decision(self, tier: str, reason: str) -> None:
        self._active = {"tier": tier, "reason": reason}
        if tier == "daily":
            return  # default path stays quiet
        if self.bus is not None:
            label = "big" if tier == "heavy" else "cloud"
            self.bus.publish(
                "status",
                "router",
                text=f"router: thinking harder — using the {label} brain ({reason})",
            )
            if tier == "heavy":
                self.bus.publish(
                    "status",
                    "router",
                    text="router: loading the big model — this can take a minute",
                )
        self._audit(f"escalate {tier}", reason)

    def _audit(self, action: str, detail: str) -> None:
        if self.db is None:
            return
        import asyncio

        coro = self.db.add_audit(
            "router", "router", json.dumps({"action": action}), "allow", 1, detail
        )
        try:
            asyncio.get_running_loop().create_task(coro)
        except RuntimeError:
            pass  # no loop (sync test context) — audit is best-effort

    # -- ChatProvider protocol ------------------------------------------------------

    async def chat(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
        **opts,
    ) -> AsyncIterator[Chunk]:
        tier, reason = await self._pick(messages, tools, opts)
        self._note_decision(tier, reason)

        # Failover chain: picked tier, then anything after it in the order,
        # ending at daily. Only a failure BEFORE the first chunk falls through
        # (a half-streamed reply can't be restarted transparently).
        chain = [tier] + [t for t in self.escalation_order if t != tier] + ["daily"]
        seen: set[str] = set()
        last_error: Exception | None = None
        for candidate in chain:
            if candidate in seen:
                continue
            seen.add(candidate)
            provider = self._provider_for(candidate)
            if provider is None:
                continue
            if candidate != tier and last_error is not None:
                if self.bus is not None:
                    self.bus.publish(
                        "status",
                        "router",
                        text=f"router: {tier} failed — falling back to {candidate}",
                    )
                self._audit(f"failover {candidate}", f"{tier} failed: {last_error}")
            emitted = False
            try:
                async for chunk in provider.chat(messages, tools=tools, **opts):
                    emitted = True
                    yield chunk
                return
            except Exception as exc:  # noqa: BLE001 — pre-stream errors fall through tiers
                if emitted:
                    raise  # mid-stream failure: surface it, don't silently restart
                last_error = exc
        if last_error is not None:
            raise last_error

    async def healthy(self) -> bool:
        return await self.daily.healthy()

    async def loaded_context_length(self) -> int | None:
        """Delegate to daily so the readiness check keeps working."""
        fn = getattr(self.daily, "loaded_context_length", None)
        return await fn() if fn else None

    @property
    def num_ctx(self) -> int:
        return getattr(self.daily, "num_ctx", 8192)


def build_provider(config: dict, *, bus=None, db=None) -> ChatProvider:
    """Assemble daily(+heavy)(+cloud) behind a RouterProvider from config.

    Falls back to the bare daily provider when no escalation target exists —
    the router is pure overhead in that case.
    """
    import os

    from core.providers.ollama import OllamaProvider

    models = config.get("models", {})
    daily_cfg = models.get("daily", {})
    daily = OllamaProvider(
        model=daily_cfg["model"],
        temperature=daily_cfg.get("temperature", 0.7),
        keep_alive=daily_cfg.get("keep_alive", "24h"),
        num_ctx=daily_cfg.get("num_ctx", 8192),
    )

    heavy = None
    heavy_cfg = models.get("heavy", {})
    if heavy_cfg.get("model"):
        heavy = OllamaProvider(
            model=heavy_cfg["model"],
            temperature=heavy_cfg.get("temperature", 0.5),
            keep_alive=heavy_cfg.get("keep_alive", "10m"),
            num_ctx=heavy_cfg.get("num_ctx", 8192),
        )

    cloud = None
    cloud_cfg = models.get("cloud", {})
    api_key = os.environ.get("GEMINI_API_KEY", "")
    if cloud_cfg.get("model") and api_key:
        from core.providers.gemini import GeminiProvider

        cloud = GeminiProvider(model=cloud_cfg["model"], api_key=api_key)

    if heavy is None and cloud is None:
        return daily
    return RouterProvider(
        daily,
        heavy=heavy,
        cloud=cloud,
        bus=bus,
        db=db,
        router_cfg=config.get("router", {}),
        heavy_cfg=heavy_cfg,
    )
