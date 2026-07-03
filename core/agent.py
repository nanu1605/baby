"""AgentCore: the plan → tool → observe loop."""

from __future__ import annotations

import json
from collections.abc import Callable

from core.prompts import system_prompt
from core.providers.base import ChatProvider
from db.database import Database
from tools import registry

MAX_TOOL_ITERATIONS = 8


class AgentCore:
    """One conversation-bound agent loop over a ChatProvider.

    Interface-agnostic: CLI/UI/voice all call run_turn() and receive
    streamed text via on_delta; tool activity via on_event.
    """

    def __init__(
        self,
        provider: ChatProvider,
        db: Database,
        conversation_id: int,
        history_limit: int = 30,
    ) -> None:
        self.provider = provider
        self.db = db
        self.conversation_id = conversation_id
        self.history_limit = history_limit

    async def run_turn(
        self,
        user_text: str,
        on_delta: Callable[[str], None] | None = None,
        on_event: Callable[[str], None] | None = None,
    ) -> str:
        """Process one user message; returns the final assistant reply."""
        await self.db.add_message(self.conversation_id, "user", user_text)
        # Only user/assistant text is reloaded across restarts; tool traffic
        # lives within a single turn (see DECISIONS.md).
        history = await self.db.get_messages(
            self.conversation_id, self.history_limit, roles=("user", "assistant")
        )
        messages: list[dict] = [{"role": "system", "content": system_prompt()}, *history]

        for _ in range(MAX_TOOL_ITERATIONS):
            text_parts: list[str] = []
            tool_calls = []
            async for chunk in self.provider.chat(messages, tools=registry.schemas()):
                if chunk.delta:
                    text_parts.append(chunk.delta)
                    if on_delta:
                        on_delta(chunk.delta)
                if chunk.tool_calls:
                    tool_calls = chunk.tool_calls
            text = "".join(text_parts)

            if not tool_calls:
                reply = text or "(no response)"
                await self.db.add_message(self.conversation_id, "assistant", reply)
                return reply

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
                if on_event:
                    on_event(f"tool: {tc.name}({tc.arguments})")
                result = await registry.dispatch(tc.name, tc.arguments)
                if on_event:
                    on_event(f"  -> {result[:200]}")
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
        return capped
