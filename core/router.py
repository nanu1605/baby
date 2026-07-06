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


class HealthMonitor:
    """NIM connectivity state machine: CLOUD / DEGRADED / OFFLINE (spec §2.2).

    One failure (timeout/429/5xx) drops CLOUD→DEGRADED; a dead network
    (DNS/connect) drops straight to OFFLINE. Recovery needs `recover_after`
    consecutive healthy probes, and the final DEGRADED→CLOUD hop additionally
    proves generation with a 1-token ping (connectivity ≠ working inference).
    A 429 starts a `cooldown_429_s` cloud cooldown regardless of state.
    Passive signals: every real call's outcome feeds the machine. Every
    transition is audited with its reason and surfaced on the bus.
    """

    def __init__(self, probe_provider, *, cfg: dict | None = None,
                 bucket=None, bus=None, db=None) -> None:
        cfg = cfg or {}
        self.provider = probe_provider  # NIM provider that owns probe()
        self.bucket = bucket
        self.bus = bus
        self.db = db
        self.probe_s = float(cfg.get("probe_s", 45))
        self.recover_after = int(cfg.get("recover_after", 3))
        self.cooldown_429_s = float(cfg.get("cooldown_429_s", 90))
        # Recovery must prove USABLE generation: the models-list GET succeeds
        # even when generation is congested past any interactive budget, and
        # an unbounded ping would flip cloud→degraded→cloud forever (observed
        # live: every recovered turn re-paid the 3.5 s first-token tax).
        self.gen_ping_timeout_s = float(cfg.get("gen_ping_timeout_s", 8))
        self.state = "cloud"
        self.cooldown_until = 0.0
        self._streak = 0
        self._task = None

    # -- lifecycle (started by the UI server once a loop exists) ----------------

    def start(self) -> None:
        import asyncio

        if self._task is None and self.provider is not None:
            self._task = asyncio.create_task(self._probe_loop(), name="baby-nim-probe")

    async def stop(self) -> None:
        import asyncio

        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
            self._task = None

    # -- signals -----------------------------------------------------------------

    def cooling_down(self) -> bool:
        import time

        return time.monotonic() < self.cooldown_until

    def note_failure(self, reason: str) -> None:
        """Passive signal from a real call: one failure flips state instantly."""
        import time

        if reason == "429":
            self.cooldown_until = time.monotonic() + self.cooldown_429_s
        new = "offline" if reason == "dns_fail" else "degraded"
        # Never upgrade on a failure (offline stays offline on a 429 report).
        if self.state == "offline" and new == "degraded":
            new = "offline"
        self._streak = 0
        self._transition(new, reason)

    def note_success(self) -> None:
        """A real NIM call succeeded — counts toward recovery like a probe."""
        if self.state == "cloud":
            return
        self._streak += 1
        if self.state == "offline":
            self._transition("degraded", "net returned (live call)")
        elif self._streak >= self.recover_after:
            # A full successful generation IS the proof the gen ping exists for.
            self._transition("cloud", "recovered")

    # -- probe loop ----------------------------------------------------------------

    async def _probe_loop(self) -> None:
        import asyncio

        while True:
            await asyncio.sleep(self.probe_s)
            try:
                await self._probe_once()
            except Exception:  # noqa: BLE001 — the probe must never die
                pass

    async def _probe_once(self) -> None:
        import asyncio

        # Probes share the NIM bucket as background traffic; a full bucket
        # means real calls are flowing — passive signals cover us.
        if self.bucket is not None and not self.bucket.try_acquire(background=True):
            return
        ok = await self.provider.probe()
        if not ok:
            self._streak = 0
            if self.state != "offline":
                self._transition("offline" if self.state == "degraded" else "degraded",
                                 "probe failed")
            return
        if self.state == "offline":
            self._streak = 0
            self._transition("degraded", "net returned")
            return
        if self.state == "degraded":
            self._streak += 1
            if self._streak >= self.recover_after:
                # Prove generation before flipping back — burns one bucket slot
                # and must land within the interactive budget or the recovery
                # is a lie (congested-but-alive stays degraded, local serves).
                if self.bucket is not None:
                    await self.bucket.acquire_wait(background=True)
                try:
                    ok = await asyncio.wait_for(
                        self.provider.probe(generation=True), self.gen_ping_timeout_s
                    )
                except TimeoutError:
                    ok = False
                if ok:
                    self._transition("cloud", "recovered")
                else:
                    self._streak = 0

    # -- reporting -------------------------------------------------------------------

    def _transition(self, new: str, reason: str) -> None:
        if new == self.state:
            return
        old, self.state = self.state, new
        if self.bus is not None:
            self.bus.publish(
                "status", "router",
                text=f"router: cloud state {old} → {new} ({reason})",
            )
        if self.db is not None:
            import asyncio

            coro = self.db.add_audit(
                "router", "router",
                json.dumps({"action": f"state {old}->{new}"}), "allow", 1, reason,
            )
            try:
                asyncio.get_running_loop().create_task(coro)
            except RuntimeError:
                pass


class CloudRouter:
    """Cloud-primary ladder (spec §2.1) behind the same ChatProvider protocol.

    Per turn:
      pinned (privacy/language, opts["pin_local"])   → local only
      OFFLINE                                        → local only
      bucket empty (overflow)                        → local only, never queue
      game mode                                      → NIM only, no local rung
      heavy turn (tier_hint="best"/planning/explicit)→ nim_heavy → nim_primary → local
      normal turn                                    → nim_primary → backstop → local

    Per-request fallback is mid-agent-loop by construction: every agent
    iteration calls chat() fresh with the full messages array, and a rung
    that fails BEFORE emitting a chunk falls through to the next one. A
    failure after emission surfaces (half-streamed replies can't restart).
    First-token timeout per channel: router.first_token_timeout_s.
    """

    name = "router"

    def __init__(
        self,
        daily: ChatProvider,
        *,
        nim_primary: ChatProvider | None = None,
        nim_heavy: ChatProvider | None = None,
        backstop: ChatProvider | None = None,
        bus=None,
        db=None,
        router_cfg: dict | None = None,
    ) -> None:
        cfg = router_cfg or {}
        self.daily = daily
        self.nim_primary = nim_primary
        self.nim_heavy = nim_heavy
        self.backstop = backstop
        self.bus = bus
        self.db = db
        self.explicit_phrases = tuple(
            p.lower() for p in cfg.get("explicit_phrases", _DEFAULT_EXPLICIT)
        )
        timeouts = cfg.get("first_token_timeout_s", {}) or {}
        self.timeout_voice = float(timeouts.get("voice", 3.5))
        self.timeout_text = float(timeouts.get("text", 8))
        rate = cfg.get("rate_limit", {}) or {}
        from core.ratelimit import TokenBucket

        self.bucket = TokenBucket(
            rpm=int(rate.get("rpm", 36)),
            background_share=float(rate.get("background_share", 0.5)),
        )
        self.monitor = HealthMonitor(
            nim_primary, cfg=cfg.get("health", {}), bucket=self.bucket, bus=bus, db=db
        )
        self.privacy_pins = set(cfg.get("privacy_pins", ["read_file", "run_shell"]))
        lang = cfg.get("language_pin", {}) or {}
        self.language_pin_enabled = bool(lang.get("enabled", True))
        self.devanagari_threshold = float(lang.get("devanagari_ratio", 0.3))
        self.game_mode = False
        self._warm_task = None
        self._active = {"tier": "nim_primary", "reason": "default"}
        # Per-brain turn counters + first-token samples for the N4 soak.
        # Samples are session-local (/stats percentiles); the durable record
        # is the "served" audit row per completed stream.
        self.turn_counts = {"daily": 0, "nim_primary": 0, "nim_heavy": 0, "backstop": 0}
        self.latency: dict[str, list[float]] = {}

    # -- lifecycle -------------------------------------------------------------------

    def start(self) -> None:
        self.monitor.start()

    async def stop(self) -> None:
        await self.monitor.stop()
        if self._warm_task is not None:
            self._warm_task.cancel()

    # -- game mode (spec §2.5) ---------------------------------------------------------

    notifier = None  # assigned at boot; announces "Baby ready" after reload

    async def set_game_mode(self, on: bool) -> str:
        """Toggle game mode: ON unloads the local brain (~5.5 GB VRAM freed,
        all routing goes cloud); OFF reloads it in the background and
        announces when warm. Returns a short human line for the caller."""
        import asyncio

        if on == self.game_mode:
            return f"game mode already {'on' if on else 'off'}"
        self.game_mode = on
        self._audit_row(f"game_mode {'on' if on else 'off'}", "toggled")
        if on:
            unload = getattr(self.daily, "unload", None)
            if unload is not None:
                try:
                    await unload()
                except Exception:  # noqa: BLE001 — eviction is best-effort; routing flips regardless
                    pass
            line = "game mode ON — local brain unloaded, cloud answers now"
        else:
            self._warm_task = asyncio.create_task(self._rewarm(), name="baby-game-rewarm")
            line = "game mode OFF — reloading the local brain in the background"
        if self.bus is not None:
            self.bus.publish("status", "router", text=f"router: {line}")
        return line

    async def _rewarm(self) -> None:
        warm = getattr(self.daily, "warm", None)
        try:
            if warm is not None:
                await warm()
            if self.bus is not None:
                self.bus.publish(
                    "status", "router", text="router: local brain warm — Baby ready"
                )
            if self.notifier is not None:
                await self.notifier.announce(
                    "Baby ready — local brain reloaded.",
                    toast_title="Baby — game mode off",
                )
        except Exception as exc:  # noqa: BLE001 — a failed rewarm must not kill anything
            if self.bus is not None:
                self.bus.publish(
                    "error", "router",
                    text=f"router: local brain reload failed ({type(exc).__name__}: {exc})",
                )

    # -- compat hooks (duck-typed by AgentCore / readiness / stats) --------------------

    def record_turn_result(self, channel: str, status: str) -> None:
        return  # per-request fallback replaces retry escalation

    @property
    def active(self) -> dict:
        info = dict(self._active)
        info["state"] = self.monitor.state
        return info

    @property
    def cloud(self) -> ChatProvider | None:
        """Gemini backstop under the legacy attribute name — VisionService
        reuses the router's Gemini instance for screenshot fallback."""
        return self.backstop

    @property
    def num_ctx(self) -> int:
        """Local brain's context size — the readiness check compares it to
        what Ollama actually loaded (same contract as the legacy router)."""
        return getattr(self.daily, "num_ctx", 8192)

    async def healthy(self) -> bool:
        return await self.daily.healthy()

    async def loaded_context_length(self) -> int | None:
        fn = getattr(self.daily, "loaded_context_length", None)
        return await fn() if fn else None

    # -- ladder -----------------------------------------------------------------------

    def _is_heavy_turn(self, messages: list[dict], opts: dict) -> bool:
        if opts.get("tier_hint") == "best":
            return True
        text = _trailing_user_text(messages).lower()
        return bool(_PLANNING_RE.search(text)) or any(
            p in text for p in self.explicit_phrases
        )

    def _pinned_tools_in(self, messages: list[dict]) -> set[str]:
        """Names of privacy-pinned tools whose results sit in this context.

        Tool-result messages carry only a tool_call_id; the id→name map comes
        from the assistant tool_calls entries earlier in the same turn.
        """
        if not self.privacy_pins:
            return set()
        id_to_name = {
            tc.get("id"): tc.get("function", {}).get("name", "")
            for m in messages
            if m.get("role") == "assistant"
            for tc in (m.get("tool_calls") or [])
        }
        return {
            name
            for m in messages
            if m.get("role") == "tool"
            for name in [id_to_name.get(m.get("tool_call_id"), "")]
            if name in self.privacy_pins
        }

    def _redact_pinned(self, messages: list[dict]) -> list[dict]:
        """Cloud-bound copy: pinned tool results become size-only placeholders.

        Defense-in-depth — the ladder already keeps pinned turns local, so a
        cloud provider should never receive these bytes; if a future ladder
        bug routes one anyway, the payload still carries no private content.
        """
        pinned = self._pinned_tools_in(messages)
        if not pinned:
            return messages
        id_to_name = {
            tc.get("id"): tc.get("function", {}).get("name", "")
            for m in messages
            if m.get("role") == "assistant"
            for tc in (m.get("tool_calls") or [])
        }
        out = []
        for m in messages:
            name = id_to_name.get(m.get("tool_call_id"), "")
            if m.get("role") == "tool" and name in pinned:
                size = len(str(m.get("content") or ""))
                out.append(
                    {**m, "content":
                     f"[local-only content redacted: {name} result, {size} bytes]"}
                )
            else:
                out.append(m)
        return out

    def _ladder(self, messages: list[dict], tools, opts: dict) -> tuple[list[str], str]:
        # Privacy outranks EVERYTHING, game mode included: pinned bytes never
        # leave the PC even if that means reloading the unloaded local brain.
        if opts.get("pin_local"):
            return ["daily"], str(opts.get("pin_reason") or "pinned")
        pinned = self._pinned_tools_in(messages)
        if pinned:
            return ["daily"], f"privacy pin ({', '.join(sorted(pinned))})"
        if self.game_mode:
            # Local is unloaded — never a rung. All-cloud, honest if all fail.
            if self._is_heavy_turn(messages, opts):
                return ["nim_heavy", "nim_primary", "backstop"], "game mode (heavy)"
            return ["nim_primary", "backstop"], "game mode"
        # Language pin outranks connectivity state (spec §2.1 lists pins as
        # the FIRST rung): a Devanagari turn during DEGRADED was routing to
        # Gemini instead of the local Qwen (caught by the live E2E battery).
        # Game mode still wins above — Hindi flows fine to the NIM primary
        # and the whole point is a free GPU.
        if self.language_pin_enabled:
            from core.prompts import devanagari_ratio

            if devanagari_ratio(_trailing_user_text(messages)) >= self.devanagari_threshold:
                return ["daily"], "language pin (Devanagari)"
        if self.monitor.state == "offline":
            return ["daily"], "offline"
        if self.monitor.state == "degraded":
            # Hysteresis means STAY fallen: re-trying NIM on every agent
            # iteration re-paid the first-token timeout each time (observed
            # live — ~3.5 s of dead air per tool step). Probes own recovery;
            # until then the backstop and the warm local brain serve.
            if opts.get("max_tokens") and not tools and opts.get("tier_hint") != "best":
                return ["daily"], "internal call"
            return ["backstop", "daily"], "degraded — waiting for probes"
        # tier_hint="best" (orchestrator planning/integration) outranks the
        # internal-call short-circuit — those calls are capped AND toolless by
        # design, yet they are exactly the turns that want the heavy brain.
        if opts.get("tier_hint") == "best":
            return ["nim_heavy", "nim_primary", "daily"], "tier_hint"
        # Internal capped calls (summary/extraction/suggestion) stay on the
        # warm local brain: free, private, and immune to cloud hiccups. Text
        # triggers are NOT consulted here — a summary containing the word
        # "plan" must not wake the heavy brain.
        if opts.get("max_tokens") and not tools:
            return ["daily"], "internal call"
        if self._is_heavy_turn(messages, opts):
            return ["nim_heavy", "nim_primary", "daily"], "heavy turn"
        return ["nim_primary", "backstop", "daily"], "normal turn"

    def _provider_for(self, tier: str) -> ChatProvider | None:
        return {
            "daily": self.daily,
            "nim_primary": self.nim_primary,
            "nim_heavy": self.nim_heavy,
            "backstop": self.backstop,
        }.get(tier)

    def _first_token_timeout(self, opts: dict) -> float:
        channel = str(opts.get("channel") or "")
        return self.timeout_voice if channel == "voice" else self.timeout_text

    # -- ChatProvider protocol -----------------------------------------------------------

    async def chat(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
        **opts,
    ) -> AsyncIterator[Chunk]:
        import asyncio

        from openai import APIConnectionError, APIStatusError

        ladder, reason = self._ladder(messages, tools, opts)
        background = str(opts.get("channel") or "").startswith("task:")
        timeout = self._first_token_timeout(opts)
        last_error: Exception | None = None

        queue = list(ladder)
        rung_no = -1
        while queue:
            tier = queue.pop(0)
            rung_no += 1
            provider = self._provider_for(tier)
            if provider is None:
                continue
            is_nim = tier in ("nim_primary", "nim_heavy")
            if is_nim:
                if self.monitor.cooling_down() or not await provider.healthy():
                    self._note_skip(tier, "cooldown/unhealthy")
                    continue
                if not self.bucket.try_acquire(background=background):
                    # Overflow skips the cloud ENTIRELY (backstop included) —
                    # straight to local, never a queue (spec §2.1).
                    self._note_skip(tier, "rate bucket empty — overflow to local")
                    queue = [] if self.game_mode else ["daily"]
                    continue
            elif tier == "backstop" and not await provider.healthy():
                self._note_skip(tier, "backstop unhealthy/cooldown")
                continue

            import time as _time

            self._note_decision(tier, reason if rung_no == 0 else f"fallback from {ladder[0]}")
            payload = messages if tier == "daily" else self._redact_pinned(messages)
            stream = provider.chat(payload, tools=tools, **opts).__aiter__()
            emitted = False
            t_start = _time.monotonic()
            ft_ms: float | None = None
            # The final rung must answer — no first-token cutoff for it.
            is_last = not queue
            try:
                while True:
                    if not emitted and not is_last:
                        try:
                            chunk = await asyncio.wait_for(stream.__anext__(), timeout)
                        except TimeoutError as exc:
                            last_error = exc
                            if is_nim:
                                self.monitor.note_failure("timeout")
                            self._note_skip(tier, f"first token > {timeout:g}s")
                            break
                    else:
                        try:
                            chunk = await stream.__anext__()
                        except StopAsyncIteration:
                            if is_nim:
                                self.monitor.note_success()
                            self._record_served(
                                tier, str(opts.get("channel") or ""), ft_ms
                            )
                            return
                    if not emitted:
                        ft_ms = (_time.monotonic() - t_start) * 1000.0
                    emitted = True
                    yield chunk
            except APIStatusError as exc:
                if emitted:
                    raise
                last_error = exc
                if is_nim:
                    self.monitor.note_failure(
                        "429" if exc.status_code == 429 else f"{exc.status_code}"
                    )
                self._note_skip(tier, f"HTTP {exc.status_code}")
            except APIConnectionError as exc:
                if emitted:
                    raise
                last_error = exc
                if is_nim:
                    self.monitor.note_failure("dns_fail")
                self._note_skip(tier, "connection failed")
            except StopAsyncIteration:
                # Timed-out branch consumed the stream end — treat as empty.
                last_error = last_error or RuntimeError(f"{tier}: empty reply")
                self._note_skip(tier, "empty reply")
            except Exception as exc:  # noqa: BLE001 — pre-stream errors fall through rungs
                if emitted:
                    raise
                last_error = exc
                if is_nim:
                    self.monitor.note_failure("error")
                self._note_skip(tier, f"{type(exc).__name__}")
            finally:
                close = getattr(stream, "aclose", None)
                if close is not None:
                    try:
                        await close()
                    except Exception:  # noqa: BLE001
                        pass

        if self.game_mode:
            # Spec §2.5: net gone during game mode → say so plainly and offer
            # the way out. Raising here left the owner with dead air AND no
            # model able to run the set_game_mode tool (observed live).
            honest = (
                "The cloud is unreachable and my local brain is unloaded "
                "(game mode). Say 'game mode off' and I'll reload it."
            )
            if self.bus is not None:
                self.bus.publish("status", "router", text="router: game mode offline")
            yield Chunk(delta=honest)
            yield Chunk(done=True)
            return
        if last_error is not None:
            raise last_error
        raise RuntimeError("router: no provider available for this turn")

    # -- reporting ------------------------------------------------------------------------

    def _note_decision(self, tier: str, reason: str) -> None:
        self._active = {
            "tier": tier,
            "reason": reason,
            "model": getattr(self._provider_for(tier), "model", ""),
        }
        self.turn_counts[tier] = self.turn_counts.get(tier, 0) + 1
        if self.bus is not None and tier != "nim_primary":
            self.bus.publish(
                "status", "router", text=f"router: using {tier} ({reason})"
            )
        self._audit_row(f"route {tier}", reason)

    def _record_served(self, tier: str, channel: str, first_token_ms: float | None) -> None:
        """Durable per-stream record: the soak report reads these audit rows."""
        if first_token_ms is not None:
            samples = self.latency.setdefault(tier, [])
            samples.append(first_token_ms)
            del samples[:-500]
        self._audit_row(
            f"served {tier}",
            json.dumps({"channel": channel, "first_token_ms":
                        round(first_token_ms, 1) if first_token_ms is not None else None}),
        )

    def _note_skip(self, tier: str, detail: str) -> None:
        if self.bus is not None:
            self.bus.publish("status", "router", text=f"router: {tier} skipped — {detail}")
        self._audit_row(f"skip {tier}", detail)

    def _audit_row(self, action: str, detail: str) -> None:
        if self.db is None:
            return
        import asyncio

        coro = self.db.add_audit(
            "router", "router", json.dumps({"action": action}), "allow", 1, detail
        )
        try:
            asyncio.get_running_loop().create_task(coro)
        except RuntimeError:
            pass


def build_nim_providers(config: dict) -> dict:
    """{"nim_primary": NvidiaProvider|None, "nim_heavy": ...} from config + env.

    A slot is built only when its model is set (N1 bench winner) AND
    NVIDIA_API_KEY is present — absent either, the slot is None and the
    cloud-primary ladder (Phase N2) simply skips it.
    """
    import os

    key = os.environ.get("NVIDIA_API_KEY", "")
    slots: dict = {"nim_primary": None, "nim_heavy": None}
    if not key:
        return slots
    from core.providers.nvidia import NIM_OPENAI_URL, NvidiaProvider

    models = config.get("models", {})
    cooldown = (
        config.get("router", {}).get("health", {}).get("cooldown_429_s", 90)
    )
    for slot in slots:
        cfg = models.get(slot, {})
        if cfg.get("model"):
            slots[slot] = NvidiaProvider(
                model=cfg["model"],
                api_key=key,
                temperature=cfg.get("temperature", 0.7),
                base_url=cfg.get("base_url", NIM_OPENAI_URL),
                cooldown_s=float(cooldown),
            )
    return slots


def build_provider(config: dict, *, bus=None, db=None) -> ChatProvider:
    """Assemble daily(+heavy)(+cloud) behind a RouterProvider from config.

    Falls back to the bare daily provider when no escalation target exists —
    the router is pure overhead in that case.
    """
    import os

    from core.providers.ollama import OllamaProvider

    mode = config.get("router", {}).get("mode", "local_primary")
    models = config.get("models", {})

    if mode == "cloud_primary":
        daily_cfg = models.get("daily", {})
        daily = OllamaProvider(
            model=daily_cfg["model"],
            temperature=daily_cfg.get("temperature", 0.7),
            keep_alive=daily_cfg.get("keep_alive", "24h"),
            num_ctx=daily_cfg.get("num_ctx", 8192),
        )
        nim = build_nim_providers(config)
        backstop = None
        cloud_cfg = models.get("cloud", {})
        gemini_key = os.environ.get("GEMINI_API_KEY", "")
        if cloud_cfg.get("model") and gemini_key:
            from core.providers.gemini import GeminiProvider

            backstop = GeminiProvider(model=cloud_cfg["model"], api_key=gemini_key)
        if nim["nim_primary"] is None:
            # No key or no model set — cloud-primary is impossible; fail loud
            # instead of silently degrading to a local-only ladder.
            raise ValueError(
                "router.mode: cloud_primary needs NVIDIA_API_KEY in .env and "
                "models.nim_primary.model set (N1 bench winner)"
            )
        return CloudRouter(
            daily,
            nim_primary=nim["nim_primary"],
            nim_heavy=nim["nim_heavy"],
            backstop=backstop,
            bus=bus,
            db=db,
            router_cfg=config.get("router", {}),
        )

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
