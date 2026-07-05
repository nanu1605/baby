"""Screen awareness tool: describe_screen (spec feature +screen)."""

from __future__ import annotations

import json

from tools.registry import tool

_vision = None  # core.vision.VisionService, injected at boot


def configure(vision) -> None:
    global _vision
    _vision = vision


@tool
async def describe_screen(question: str = "") -> str:
    """Look at the owner's current screen and describe it, or answer a
    question about what is visible (use for "what's on my screen?")."""
    if _vision is None:
        return json.dumps({"error": "screen awareness is not available"})
    return json.dumps(await _vision.describe(question), ensure_ascii=False)
