"""AgentCore: the plan → tool → observe loop.

Interface-agnostic: every surface (CLI, UI, later voice/telegram) calls
run_turn() and watches the shared EventBus. Every tool call passes through
the safety gate and lands in audit_log — the model cannot approve its own
actions.
"""

from __future__ import annotations

import asyncio
import json

from core.bus import EventBus
from core.prompts import system_prompt
from core.providers.base import ChatProvider, ToolCall
from core.safety import SafetyClass, SafetyConfig, SafetyGate
from db.database import Database
from tools import registry

MAX_TOOL_ITERATIONS = 8


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
    ) -> None:
        self.provider = provider
        self.db = db
        self.conversation_id = conversation_id
        self.channel = channel
        self.bus = bus or EventBus()  # subscriber-less bus = no-op
        self.gate = gate or SafetyGate(SafetyConfig(), self.bus)
        self.history_limit = history_limit

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
            reply, status = "(cancelled)", "cancelled"
            await self.db.add_message(self.conversation_id, "assistant", reply)
            raise
        finally:
            self.bus.publish("turn_end", self.channel, reply=reply, status=status)

    async def _loop(self, user_text: str) -> tuple[str, str]:
        # Only user/assistant text is reloaded across restarts; tool traffic
        # lives within a single turn (see DECISIONS.md).
        history = await self.db.get_messages(
            self.conversation_id, self.history_limit, roles=("user", "assistant")
        )
        messages: list[dict] = [{"role": "system", "content": system_prompt()}, *history]

        for _ in range(MAX_TOOL_ITERATIONS):
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
                reply = text or "(no response)"
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

    async def _execute_tool(self, tc: ToolCall, user_text: str) -> str:
        """Gate → execute → audit → events. Owns the whole tool lifecycle."""
        try:
            kwargs = json.loads(tc.arguments) if tc.arguments.strip() else {}
            if not isinstance(kwargs, dict):
                kwargs = {}
        except json.JSONDecodeError:
            kwargs = {}

        verdict = self.gate.classify(tc.name, kwargs, user_text)
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
