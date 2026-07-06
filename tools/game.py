"""set_game_mode: free the GPU for games (NIM plan §2.5).

ON unloads the local 9B (~5.5 GB VRAM back to the game) and routes every
turn to the cloud; OFF reloads it in the background and Baby announces when
warm. Configured with the router at boot — inert (helpful error) when the
cloud-primary router isn't active.
"""

from __future__ import annotations

import json
import re

from tools.registry import tool

_router = None

# Deterministic escape hatch (observed live deadlock: game mode ON + cloud
# congested + Gemini cooling = no brain left to CALL the tool that turns game
# mode off). Bare commands toggle directly in the UI/voice layers, no model.
_GAME_CMD_RE = re.compile(r"^(?:baby[,!]?\s+)?(?:game|gaming)\s*mode\s+(on|off)$")


def parse_game_command(text: str) -> bool | None:
    """True/False for a bare 'game mode on/off' command, None otherwise."""
    normalized = " ".join(re.sub(r"[^\w\s]", " ", text.lower()).split())
    match = _GAME_CMD_RE.match(normalized)
    return None if match is None else match.group(1) == "on"


def configure(router) -> None:
    global _router
    _router = router


@tool
async def set_game_mode(on: bool) -> str:
    """Game mode: free the GPU for a game (on=true) or reload Baby's local brain (on=false)."""
    if _router is None or not hasattr(_router, "set_game_mode"):
        return json.dumps(
            {"error": "game mode needs the cloud-primary router (router.mode: cloud_primary)"}
        )
    line = await _router.set_game_mode(bool(on))
    return json.dumps({"game_mode": bool(on), "status": line})
