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
from core.prompts import detect_language, system_prompt
from core.providers.base import ChatProvider, ToolCall
from core.safety import SafetyClass, SafetyConfig, SafetyGate
from db.database import Database
from tools import registry

if TYPE_CHECKING:
    from memory import Memory

MAX_TOOL_ITERATIONS = 8

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

    async def run_turn(self, user_text: str) -> str:
        """Process one user message; returns the final assistant reply."""
        await self.db.add_message(self.conversation_id, "user", user_text)
        self.bus.publish("turn_start", self.channel, conversation_id=self.conversation_id)
        status = "error"
        reply = ""
        try:
            reply, status = await self._loop(user_text)
            return reply
        except asyncio.CancelledError:
            # The marker must read as CLOSURE to the model: a bare "(cancelled)"
            # left the request looking unanswered and later turns re-answered it.
            reply, status = (
                "(stopped by the user — request abandoned, do not answer or resume it)",
                "cancelled",
            )
            await self.db.add_message(self.conversation_id, "assistant", reply)
            raise
        finally:
            self.bus.publish("turn_end", self.channel, reply=reply, status=status)
            # Router hook (Phase 4): lets the provider arm retry_after_failure
            # escalation for this channel's next turn. No-op for plain providers.
            record = getattr(self.provider, "record_turn_result", None)
            if record is not None:
                record(self.channel, status)
            if self.memory is not None and status == "ok":
                self._schedule_maintenance()

    async def _loop(self, user_text: str) -> tuple[str, str]:
        summary: str | None = None
        memories: list[str] = []
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
            {"role": "system", "content": system_prompt(summary, memories, language)},
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
                "unavailable or not configured — the tool list in THIS request "
                "is the only truth; try the tool before saying it can't be done.",
            },
        ]

        tools_succeeded = 0
        intent_retried = False
        for _ in range(self.max_iterations):
            text_parts: list[str] = []
            tool_calls: list[ToolCall] = []
            async for chunk in self.provider.chat(messages, tools=registry.schemas()):
                if chunk.delta:
                    text_parts.append(chunk.delta)
                    self.bus.publish("token", self.channel, text=chunk.delta)
                if chunk.tool_calls:
                    tool_calls = chunk.tool_calls
            text = "".join(text_parts)

            if not tool_calls:
                # Thinking models can spend the whole generation window in the
                # reasoning channel after a long tool loop and emit no content
                # (observed live on the first background task). One no-tools
                # retry with thinking off forces a plain answer from the tool
                # results already in context.
                if not text.strip() and tools_succeeded > 0:
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
                reply = text or "(no response)"
                # Skip when the model already wrote a "Next:" line itself —
                # it mimics suggestions seen in history, and appending a real
                # one produced double "Next:" lines (observed live).
                if self.suggest_next_step and tools_succeeded > 0 and "Next:" not in text:
                    suggestion = await self._suggest_next_step(messages, text)
                    if suggestion:
                        reply = f"{reply}\n\nNext: {suggestion}"
                await self.db.add_message(self.conversation_id, "assistant", reply)
                return reply, "ok"

            messages.append(
                {
                    "role": "assistant",
                    "content": text or None,
                    "tool_calls": [
                        {
                            "id": tc.id,
                            "type": "function",
                            "function": {"name": tc.name, "arguments": tc.arguments},
                        }
                        for tc in tool_calls
                    ],
                }
            )
            for tc in tool_calls:
                result = await self._execute_tool(tc, user_text)
                if not result.lstrip().startswith('{"error"'):
                    tools_succeeded += 1
                await self.db.add_message(
                    self.conversation_id,
                    "tool",
                    json.dumps({"name": tc.name, "result": result}),
                )
                messages.append({"role": "tool", "tool_call_id": tc.id, "content": result})

        capped = (
            "I hit my tool-step limit for this request. Here's where I got to: "
            "several tool calls ran (see activity above). Want me to continue?"
        )
        await self.db.add_message(self.conversation_id, "assistant", capped)
        return capped, "capped"

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
        except Exception:  # noqa: BLE001 — fall back to the "(no response)" placeholder
            return ""
        return "".join(parts).strip()

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
                    "content": "In one short line, suggest the single most useful "
                    "next step after what you just did. No preamble, no options — "
                    "just the suggestion, in the same language as my previous message.",
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
