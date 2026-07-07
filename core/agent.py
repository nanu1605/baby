"""AgentCore: the plan → tool → observe loop.

Interface-agnostic: every surface (CLI, UI, later voice/telegram) calls
run_turn() and watches the shared EventBus. Every tool call passes through
the safety gate and lands in audit_log — the model cannot approve its own
actions.
"""

from __future__ import annotations

import asyncio
import json
import re
from typing import TYPE_CHECKING

from core.bus import EventBus
from core.context import sanitize_messages
from core.intents import (
    is_wipe_confirmation,
    parse_memory_command,
    parse_no,
    parse_yes,
)
from core.prompts import detect_language, system_prompt
from core.providers.base import ChatProvider, ToolCall
from core.safety import SafetyClass, SafetyConfig, SafetyGate
from db.database import Database
from tools import registry

if TYPE_CHECKING:
    from memory import Memory

MAX_TOOL_ITERATIONS = 8

# Served when a generation comes back empty even after the _final_answer retry.
# Silence is never an acceptable reply, and the bare "(no response)" placeholder
# read to owners as a dead assistant — this asks for a redo instead.
_EMPTY_REPLY_FALLBACK = "I hit a snag generating a response — mind trying that once more?"

# Substrings that mark a provider 4xx/context rejection (as opposed to a network
# blip). A match triggers the P2 self-heal: rebuild from the rolling summary and
# retry once, rather than failing the turn on replayed DB debris.
_CONTEXT_ERROR_HINTS = (
    "invalid_request",
    "tool_call",
    "tool_calls",
    "must be a response to",
    "must be followed by",
    "did not have response messages",
    "unexpected role",
    "messages with role",
)


def _looks_like_context_error(exc: Exception) -> bool:
    text = str(exc).lower()
    if "400" in text and "message" in text:
        return True
    return any(hint in text for hint in _CONTEXT_ERROR_HINTS)


def _format_past_context(hits: list[dict] | None) -> str | None:
    """Dated one-line snippets of retrieved past exchanges (P4 cross-session RAG)."""
    if not hits:
        return None
    lines = []
    for hit in hits:
        date = str(hit.get("created_at") or "")[:10]
        who = "user" if hit.get("role") == "user" else "Baby"
        text = " ".join(str(hit.get("text") or "").split())
        if len(text) > 240:
            text = text[:240] + "…"
        lines.append(f"- [{date}] {who}: {text}")
    return "\n".join(lines) or None

# "I'll open Yahoo and run that search for you." — full stop, zero tool calls
# (observed live, repeatedly, after cancelled turns). A promise of action with
# no action gets ONE deterministic retry telling the model to act now.
_INTENT_ONLY_RE = re.compile(
    r"^\s*(?:(?:sure|okay|ok|right|alright)[,!\s]+)?"
    r"(?:opening\b|(?:i'?ll|i\s+will|let\s+me)\s+"
    r"(?:open|go|search|run|start|check|look|get|fetch|take|close|launch"
    r"|browse|find|read|type|press|try|pull|research)\b)",
    re.IGNORECASE,
)


def _summarize(text: str, limit: int = 300) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _valid_args(arguments: str) -> str:
    """Tool-call arguments, coerced to valid JSON for the message history."""
    try:
        json.loads(arguments or "{}")
        return arguments or "{}"
    except (ValueError, TypeError):
        return "{}"


_THINK_BLOCK_RE = re.compile(r"<think>.*?</think>", re.DOTALL)


def _scrub_think(text: str) -> str:
    """Reasoning-channel text that leaked into content, removed.

    Qwen over /v1 sometimes omits the opening <think> tag — Ollama then can't
    split the reasoning channel, so the reasoning streams as content followed
    by a stray </think> and a restated answer (observed live in the E2E
    battery's memory test). Everything before an unpaired close is reasoning,
    not answer; paired blocks are dropped whole.
    """
    text = _THINK_BLOCK_RE.sub("", text)
    if "</think>" in text:
        text = text.rsplit("</think>", 1)[1]
    return text.strip()


def _render_command(tool: str, kwargs: dict) -> str:
    """Human-readable action line for confirmation prompts."""
    if tool == "run_shell":
        return str(kwargs.get("command", ""))
    if tool == "write_file":
        return f"write_file {kwargs.get('mode', 'create')} → {kwargs.get('path', '')}"
    if tool == "app_control":
        return f"{kwargs.get('action', '?')} {kwargs.get('name', '')}"
    return f"{tool}({json.dumps(kwargs, ensure_ascii=False)[:200]})"


class AgentCore:
    """One conversation-bound agent loop over a ChatProvider."""

    def __init__(
        self,
        provider: ChatProvider,
        db: Database,
        conversation_id: int,
        *,
        channel: str = "cli",
        bus: EventBus | None = None,
        gate: SafetyGate | None = None,
        history_limit: int = 30,
        memory: Memory | None = None,
        suggest_next_step: bool = False,
        max_iterations: int = MAX_TOOL_ITERATIONS,
    ) -> None:
        self.provider = provider
        self.db = db
        self.conversation_id = conversation_id
        self.channel = channel
        self.bus = bus or EventBus()  # subscriber-less bus = no-op
        self.gate = gate or SafetyGate(SafetyConfig(), self.bus)
        self.history_limit = history_limit
        self.memory = memory
        self.suggest_next_step = suggest_next_step
        self.max_iterations = max_iterations
        self.maintenance_task: asyncio.Task | None = None  # clients may await on shutdown
        # P3 proceed/cancel: the next-step suggestion this turn offered, armed for
        # exactly ONE follow-up turn (a "haan"/"no" answer). One-shot.
        self.pending_suggestion: str | None = None
        # P4 wipe-all: a "wipe all memory" arms this one-shot challenge; only the
        # next turn's explicit confirmation erases everything.
        self.pending_wipe: bool = False
        # P5 telemetry: token usage summed across every model call of one turn
        # (main loop rounds + final-answer + next-step). Reset per run_turn.
        self._turn_tokens: dict = {"prompt": 0, "completion": 0, "total": 0}

    def _accrue_tokens(self, usage: dict | None) -> None:
        """Add one generation's reported usage to this turn's running total (P5)."""
        if not usage:
            return
        self._turn_tokens["prompt"] += int(usage.get("prompt_tokens", 0) or 0)
        self._turn_tokens["completion"] += int(usage.get("completion_tokens", 0) or 0)
        self._turn_tokens["total"] += int(usage.get("total_tokens", 0) or 0)

    async def run_turn(self, user_text: str) -> str:
        """Process one user message; returns the final assistant reply."""
        self._turn_tokens = {"prompt": 0, "completion": 0, "total": 0}  # P5 fresh count
        # P4: deterministic memory commands (new chat / forget that / wipe) run
        # model-free and reach every channel here (all funnel through run_turn).
        mem_action = self._memory_action(user_text)
        if mem_action is not None:
            return await self._run_memory_command(mem_action, user_text)

        # P3 proceed/cancel: if the previous turn offered a next step, a short
        # yes/no answer resolves it. One-shot — consumed or expired this turn.
        pending, self.pending_suggestion = self.pending_suggestion, None
        proceed = bool(pending) and parse_yes(user_text)
        decline = bool(pending) and not proceed and parse_no(user_text)

        # Every row of this turn shares a turn_id so a failure can quarantine
        # the whole turn atomically and keep its debris out of future context.
        turn_id = await self.db.next_turn_id(self.conversation_id)
        await self.db.add_message(self.conversation_id, "user", user_text, turn_id=turn_id)
        self.bus.publish("turn_start", self.channel, conversation_id=self.conversation_id)
        status = "error"
        reply = ""
        try:
            if decline:
                # Proposed next step declined — acknowledge, don't act, no model
                # call. (A CONFIRM-class action was never queued, so nothing to
                # cancel at the gate.) Match the user's language.
                reply = (
                    "ठीक है, छोड़ देता हूँ।"
                    if detect_language(user_text) == "Hindi"
                    else "Okay, I'll skip that."
                )
                self.bus.publish("token", self.channel, text=reply)
                await self.db.add_message(
                    self.conversation_id, "assistant", reply, turn_id=turn_id
                )
                status = "ok"
            else:
                reply, status = await self._loop(
                    user_text, turn_id, proceed_hint=pending if proceed else None
                )
            return reply
        except asyncio.CancelledError:
            # The marker must read as CLOSURE to the model: a bare "(cancelled)"
            # left the request looking unanswered and later turns re-answered it.
            reply, status = (
                "(stopped by the user — request abandoned, do not answer or resume it)",
                "cancelled",
            )
            await self.db.add_message(self.conversation_id, "assistant", reply, turn_id=turn_id)
            raise
        finally:
            # A turn that errored is debris: quarantine every row so it never
            # re-enters context (P2). Cancelled turns keep their closure marker.
            if status == "error":
                try:
                    await self.db.mark_turn(self.conversation_id, turn_id, "failed")
                except Exception:  # noqa: BLE001 — never mask the original failure
                    pass
            elif status in ("ok", "capped"):
                # Concurrency repair: another connection's boot reconcile may have
                # flipped this turn's user row to 'failed' while we were streaming.
                # A completed turn is all-'ok', so restore the whole turn.
                try:
                    await self.db.mark_turn(self.conversation_id, turn_id, "ok")
                except Exception:  # noqa: BLE001 — best-effort repair
                    pass
            # Snapshot BEFORE maintenance spawns — its internal calls overwrite
            # the router's active decision within milliseconds. The badge shows
            # the brain that authored the final answer of this turn.
            brain = dict(getattr(self.provider, "active", None) or {})
            # P5: persist this turn's token spend (skip cancelled — awaiting the
            # DB while a CancelledError unwinds is fragile, and a cancelled turn
            # is debris anyway). Best-effort: telemetry never fails a turn.
            tokens = dict(self._turn_tokens)
            if status != "cancelled" and tokens["total"] > 0:
                try:
                    await self.db.add_usage(
                        self.conversation_id, turn_id, self.channel,
                        brain.get("tier"), brain.get("model"), tokens,
                    )
                except Exception:  # noqa: BLE001 — telemetry is never load-bearing
                    pass
            self.bus.publish(
                "turn_end", self.channel, reply=reply, status=status,
                brain=brain, tokens=tokens,
            )
            # Router hook (Phase 4): lets the provider arm retry_after_failure
            # escalation for this channel's next turn. No-op for plain providers.
            record = getattr(self.provider, "record_turn_result", None)
            if record is not None:
                record(self.channel, status)
            if self.memory is not None and status == "ok":
                self._schedule_maintenance()
            # A next-step offer only stays armed if THIS turn fully persisted its
            # assistant row (the "Next: …" the model would act on). An errored or
            # capped turn must not leave a stale suggestion for the next turn
            # (review #7).
            if status != "ok":
                self.pending_suggestion = None

    def _memory_action(self, user_text: str) -> str | None:
        """Classify a deterministic memory command, consuming the wipe challenge.

        Returns 'new_chat' | 'clear' | 'forget_last' | 'wipe' | 'wipe_confirm',
        or None for a normal turn. A pending wipe challenge is one-shot: a
        confirmation completes it; anything else cancels it and is re-checked as
        a fresh command (so a stray 'yes' never erases memory)."""
        if self.pending_wipe:
            self.pending_wipe = False
            if is_wipe_confirmation(user_text):
                return "wipe_confirm"
        return parse_memory_command(user_text)

    async def _run_memory_command(self, action: str, user_text: str) -> str:
        """Execute a memory command as a model-free, still-rendered turn."""
        self.bus.publish("turn_start", self.channel, conversation_id=self.conversation_id)
        self.pending_suggestion = None  # a memory command changes direction
        reply = ""
        try:
            hindi = detect_language(user_text) == "Hindi"
            store = self.memory.store if self.memory is not None else None
            if action in ("new_chat", "clear"):
                self.conversation_id = await self.db.create_conversation(self.channel)
                reply = "नई बातचीत शुरू कर दी।" if hindi else "Started a fresh conversation."
            elif action == "forget_last":
                result = (
                    await store.forget_last() if store else {"error": "memory unavailable"}
                )
                if result.get("forgotten"):
                    reply = (
                        f"ठीक है, यह भूल गया: {result['forgotten'][0]}"
                        if hindi
                        else f"Done — forgotten: {result['forgotten'][0]}"
                    )
                else:
                    reply = (
                        "भूलने के लिए हाल में कुछ नहीं है।"
                        if hindi
                        else "Nothing recent to forget."
                    )
            elif action == "wipe":
                if store is None:
                    reply = (
                        "याददाश्त उपलब्ध नहीं है — मिटाने के लिए कुछ नहीं।"
                        if hindi
                        else "Memory isn't available — there's nothing to wipe."
                    )
                else:
                    self.pending_wipe = True
                    reply = (
                        "इससे मेरी पूरी याददाश्त — सभी तथ्य और पुरानी बातचीत — मिट जाएगी। "
                        "पक्का करने के लिए कहें: confirm wipe।"
                        if hindi
                        else "This will erase ALL my memory — facts and past conversations. "
                        "Say 'confirm wipe' to proceed, or anything else to cancel."
                    )
            elif action == "wipe_confirm":
                if store is None:
                    reply = (
                        "याददाश्त उपलब्ध नहीं है — कुछ नहीं मिटाया।"
                        if hindi
                        else "Memory isn't available — nothing was wiped."
                    )
                else:
                    counts = await store.wipe_all()
                    # Flush the live session: a fresh conversation so nothing
                    # lingers in context until a restart.
                    self.conversation_id = await self.db.create_conversation(self.channel)
                    await self.db.add_audit(
                        self.channel, "wipe_memory", "{}", "allow", 1,
                        f"wiped {counts.get('facts', 0)} facts, "
                        f"{counts.get('messages', 0)} messages",
                    )
                    reply = (
                        "सब कुछ मिटा दिया। अब मुझे कुछ याद नहीं।"
                        if hindi
                        else "Memory wiped — I don't remember anything now."
                    )
            self.bus.publish("token", self.channel, text=reply)
        finally:
            brain = dict(getattr(self.provider, "active", None) or {})
            self.bus.publish("turn_end", self.channel, reply=reply, status="ok", brain=brain)
        return reply

    async def _loop(
        self, user_text: str, turn_id: int, *, proceed_hint: str | None = None
    ) -> tuple[str, str]:
        summary: str | None = None
        memories: list[str] = []
        past_context: str | None = None
        after_id = 0
        if self.memory is not None:
            try:
                summary, upto = await self.db.get_summary_state(self.conversation_id)
                if summary:
                    # Summarized turns must not also appear verbatim — that
                    # double-spends the 8K context on the same messages.
                    after_id = upto
                memories = [f["text"] for f in await self.memory.store.search(user_text)]
            except Exception:  # noqa: BLE001 — memory failure must not block a turn
                summary, memories, after_id = None, [], 0
            # Cross-session RAG (P4, engine v2): surface out-of-window past
            # exchanges. Excludes this conversation's rows already in the raw
            # history so it only adds genuinely older context.
            rag_k = getattr(self.memory, "rag_k", 0)
            if rag_k:
                try:
                    past = await self.memory.store.search_messages(
                        user_text,
                        k=rag_k,
                        exclude_conversation=self.conversation_id,
                    )
                    past_context = _format_past_context(past)
                except Exception:  # noqa: BLE001 — RAG is best-effort, never blocks
                    past_context = None

        # Only user/assistant text is reloaded across restarts; tool traffic
        # lives within a single turn (see DECISIONS.md).
        history = await self.db.get_messages(
            self.conversation_id,
            self.history_limit,
            roles=("user", "assistant"),
            after_id=after_id,
        )
        language = detect_language(user_text)
        messages: list[dict] = [
            {
                "role": "system",
                "content": system_prompt(summary, memories, language, past_context),
            },
            *history,
            # Trailing nudge: the head-of-prompt language pin alone loses to a
            # history full of another language (observed live with the 9B) —
            # an instruction adjacent to generation is what holds. Same for
            # capability claims: the model repeated its own stale "browser is
            # not configured" excuses from history instead of using the fixed
            # tool (observed live), so trust only the current tool list.
            {
                "role": "system",
                "content": f"Reply ONLY in {language}. Match the tone of the "
                "latest message: professional and emoji-free for work or serious "
                "questions, playful only for casual chat. Ignore any claims in "
                "this conversation OR its summary that a tool is broken, "
                "unavailable, not configured, or that you lack a capability "
                "(screenshots: browser_act action=screenshot for the browser "
                "page, describe_screen for the whole screen) — the tool list "
                "in THIS request is the only truth; try the tool before "
                "saying it can't be done.",
            },
        ]

        # Strict gate (P2): whatever the DB or a half-written turn held, the
        # payload we send is OpenAI-valid. Rows it drops are audited once.
        dropped: list[dict] = []
        messages = sanitize_messages(messages, dropped)
        if dropped:
            await self.db.add_audit(
                self.channel,
                "context_sanitizer",
                "{}",
                "allow",
                1,
                f"dropped {len(dropped)} poisoned context row(s)",
            )

        # P3 proceed: the user approved the next step this loop offered last turn.
        # Push the model to act on it now (tools still pass through the gate).
        if proceed_hint:
            messages.append(
                {
                    "role": "system",
                    "content": "The user just approved the next step you proposed "
                    f'("{proceed_hint}"). Carry it out NOW with the appropriate '
                    "tool — do not merely restate it.",
                }
            )

        tools_succeeded = 0
        tools_attempted = 0
        intent_retried = False
        healed = False
        for _idx in range(self.max_iterations):
            try:
                text, tool_calls = await self._stream(messages)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 — self-heal a rejected context once
                # Only the FIRST generation self-heals: a rejection after tool
                # calls have run would discard their results, so re-raise (the
                # turn is quarantined) rather than silently rebuild from summary.
                if healed or _idx > 0 or not _looks_like_context_error(exc):
                    raise
                healed = True
                self.bus.publish(
                    "status",
                    self.channel,
                    text="Context was rejected — rebuilding from summary and retrying.",
                )
                messages = sanitize_messages(
                    self._recovery_context(summary, memories, language, user_text)
                )
                text, tool_calls = await self._stream(messages)

            if not tool_calls:
                # Thinking models can spend the whole generation window in the
                # reasoning channel and emit no content (observed live on the
                # first background task, and on plain no-tool turns — E2E T09
                # returned "(no response)" three runs straight). One no-tools
                # retry with thinking off forces a plain answer from whatever
                # is in context; silence is never an acceptable reply.
                if not text.strip():
                    text = await self._final_answer(messages) or text
                # A promise with no action ("I'll open Yahoo…", zero tool
                # calls) leaves the request undone and the owner repeating
                # himself — push once for the actual tool call.
                if (
                    not intent_retried
                    and tools_succeeded == 0
                    and _INTENT_ONLY_RE.match(text.strip())
                ):
                    intent_retried = True
                    messages.append({"role": "assistant", "content": text})
                    messages.append(
                        {
                            "role": "system",
                            "content": "You promised an action but called no tool. "
                            "Call the required tool NOW — do not reply with another promise.",
                        }
                    )
                    continue
                if text.strip():
                    reply = text
                else:
                    # Empty even after the _final_answer retry above: serve an
                    # honest line, never the bare "(no response)" placeholder,
                    # and record the silence so it is visible in the audit trail.
                    reply = _EMPTY_REPLY_FALLBACK
                    await self.db.add_audit(
                        self.channel,
                        "generation",
                        "{}",
                        "allow",
                        1,
                        "empty model output — served honest fallback",
                    )
                # Skip when the model already wrote a "Next:" line itself —
                # it mimics suggestions seen in history, and appending a real
                # one produced double "Next:" lines (observed live).
                if (
                    self.suggest_next_step
                    and text.strip()
                    and tools_succeeded > 0
                    and "Next:" not in text
                ):
                    suggestion = await self._suggest_next_step(messages, text)
                    if suggestion:
                        reply = f"{reply}\n\nNext: {suggestion}"
                        # Arm proceed/cancel for the next turn's yes/no answer.
                        self.pending_suggestion = suggestion
                await self.db.add_message(
                    self.conversation_id, "assistant", reply, turn_id=turn_id
                )
                return reply, "ok"

            messages.append(
                {
                    "role": "assistant",
                    "content": text or None,
                    "tool_calls": [
                        {
                            "id": tc.id,
                            "type": "function",
                            # Malformed arguments (models DO emit garbage) are
                            # replayed to every later rung of the router ladder
                            # and strict backends 400 on them (Gemini, Ollama —
                            # both observed live). The dispatch error result
                            # already tells the story; history stays wire-valid.
                            "function": {"name": tc.name, "arguments": _valid_args(tc.arguments)},
                        }
                        for tc in tool_calls
                    ],
                }
            )
            for tc in tool_calls:
                tools_attempted += 1
                result = await self._execute_tool(tc, user_text)
                if not result.lstrip().startswith('{"error"'):
                    tools_succeeded += 1
                await self.db.add_message(
                    self.conversation_id,
                    "tool",
                    json.dumps({"name": tc.name, "result": result}),
                    turn_id=turn_id,
                )
                messages.append({"role": "tool", "tool_call_id": tc.id, "content": result})

        capped = (
            "I hit my tool-step limit for this request. Here's where I got to: "
            "several tool calls ran (see activity above). Want me to continue?"
        )
        await self.db.add_message(self.conversation_id, "assistant", capped, turn_id=turn_id)
        return capped, "capped"

    async def _stream(self, messages: list[dict]) -> tuple[str, list[ToolCall]]:
        """One provider generation → (scrubbed text, tool calls), tokens on the bus."""
        text_parts: list[str] = []
        tool_calls: list[ToolCall] = []
        # channel rides along for the router's per-channel first-token timeout
        # (voice falls to local faster than text); plain providers ignore it.
        async for chunk in self.provider.chat(
            messages, tools=registry.schemas(), channel=self.channel
        ):
            if chunk.delta:
                text_parts.append(chunk.delta)
                self.bus.publish("token", self.channel, text=chunk.delta)
            if chunk.tool_calls:
                tool_calls = chunk.tool_calls
            if chunk.usage:
                self._accrue_tokens(chunk.usage)
        return _scrub_think("".join(text_parts)), tool_calls

    def _recovery_context(
        self, summary: str | None, memories: list[str], language: str, user_text: str
    ) -> list[dict]:
        """Last-known-good context: the rolling summary + memories + this turn.

        Used by the self-heal path when a provider rejects the replayed history.
        Dropping the raw history and leaning on the summary keeps the turn alive
        without the debris that triggered the rejection.
        """
        return [
            {"role": "system", "content": system_prompt(summary, memories, language)},
            {"role": "user", "content": user_text},
        ]

    async def _final_answer(self, messages: list[dict]) -> str:
        """Force a plain-text answer when the final model call came back empty."""
        nudge = {
            "role": "system",
            "content": "Answer now in plain text using the tool results above. Do not call tools.",
        }
        parts: list[str] = []
        try:
            async for chunk in self.provider.chat(
                [*messages, nudge], tools=None, max_tokens=700, reasoning_effort="none"
            ):
                if chunk.delta:
                    parts.append(chunk.delta)
                    self.bus.publish("token", self.channel, text=chunk.delta)
                if chunk.usage:
                    self._accrue_tokens(chunk.usage)
        except Exception:  # noqa: BLE001 — fall back to the "(no response)" placeholder
            return ""
        return _scrub_think("".join(parts))

    async def _suggest_next_step(self, messages: list[dict], final_text: str) -> str:
        """Feature #8: one extra no-tools call proposing the next action.

        Failure is soft — a missing suggestion never fails the turn. The
        "\\n\\nNext: " prefix streams only once real tokens arrive, so an
        empty suggestion leaves no dangling text in the UI.
        """
        try:
            prompt = [
                *messages,
                {"role": "assistant", "content": final_text or "(task done)"},
                {
                    "role": "user",
                    "content": "In one short line, OFFER the single most useful next "
                    "step as a question I can answer yes/no (e.g. 'Want me to … ?'). "
                    "No preamble, no options — just the offer, in the same language "
                    "as my previous message.",
                },
            ]
            parts: list[str] = []
            async for chunk in self.provider.chat(
                prompt, tools=None, max_tokens=80, reasoning_effort="none"
            ):
                if chunk.delta:
                    if not parts:
                        self.bus.publish("token", self.channel, text="\n\nNext: ")
                    parts.append(chunk.delta)
                    self.bus.publish("token", self.channel, text=chunk.delta)
                if chunk.usage:
                    self._accrue_tokens(chunk.usage)
            return "".join(parts).strip()
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 — suggestion is best-effort
            return ""

    def _schedule_maintenance(self) -> None:
        """Single-flight background summarize + extract after a good turn."""
        if self.maintenance_task is not None and not self.maintenance_task.done():
            return
        self.maintenance_task = asyncio.create_task(self._maintenance())

    async def _maintenance(self) -> None:
        try:
            await self.memory.summarizer.maybe_summarize(self.conversation_id)
        except Exception:  # noqa: BLE001 — maintenance never disturbs the session
            pass
        try:
            await self.memory.extractor.maybe_extract(self.conversation_id)
        except Exception:  # noqa: BLE001
            pass
        # Live cross-session RAG embedding (P4, engine v2): embed this turn's new
        # messages so they are searchable immediately — off the reply's path.
        if getattr(self.memory, "rag_k", 0):
            try:
                await self.memory.store.embed_new_messages(self.conversation_id)
            except Exception:  # noqa: BLE001
                pass

    async def _execute_tool(self, tc: ToolCall, user_text: str) -> str:
        """Gate → execute → audit → events. Owns the whole tool lifecycle."""
        try:
            kwargs = json.loads(tc.arguments) if tc.arguments.strip() else {}
            if not isinstance(kwargs, dict):
                kwargs = {}
        except json.JSONDecodeError:
            kwargs = {}

        verdict = self.gate.classify(tc.name, kwargs, user_text, channel=self.channel)
        args_json = json.dumps(kwargs, ensure_ascii=False)
        event_args = kwargs if len(args_json) <= 2048 else {"_truncated": args_json[:2048]}
        self.bus.publish(
            "tool_start",
            self.channel,
            call_id=tc.id,
            tool=tc.name,
            args=event_args,
            safety_class=str(verdict.klass),
        )

        approved, exec_status = 1, "ok"
        result = ""
        if verdict.klass is SafetyClass.DENY:
            result = json.dumps({"error": f"denied by safety gate: {verdict.reason}"})
            approved, exec_status = 0, "denied"
        elif verdict.klass is SafetyClass.CONFIRM:
            ok, resolution = await self.gate.confirmations.ask(
                tool=tc.name,
                command=_render_command(tc.name, kwargs),
                explanation=verdict.reason,
                channel=self.channel,
            )
            if not ok:
                result = json.dumps({"error": f"user confirmation {resolution} — not executed"})
                approved, exec_status = 0, resolution
            else:
                # Let the gate remember what this approval covers (e.g. a
                # browser domain for the rest of the session).
                self.gate.note_approval(tc.name, kwargs)

        if approved:
            if self.gate.dry_run and verdict.klass is not SafetyClass.ALLOW:
                result = json.dumps(
                    {"dry_run": True, "would_run": _render_command(tc.name, kwargs)}
                )
                exec_status = "dry_run"
            else:
                result = await registry.dispatch(tc.name, tc.arguments)
                exec_status = "error" if result.lstrip().startswith('{"error"') else "ok"

        await self.db.add_audit(
            self.channel,
            tc.name,
            args_json[:2048],
            str(verdict.klass),
            approved,
            _summarize(result),
        )
        self.bus.publish(
            "tool_end",
            self.channel,
            call_id=tc.id,
            tool=tc.name,
            safety_class=str(verdict.klass),
            status=exec_status,
            result_summary=_summarize(result),
        )
        return result
