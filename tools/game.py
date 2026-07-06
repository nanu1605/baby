"""set_game_mode: free the GPU for games (NIM plan §2.5).

ON unloads the local 9B (~5.5 GB VRAM back to the game) and routes every
turn to the cloud; OFF reloads it in the background and Baby announces when
warm. Configured with the router at boot — inert (helpful error) when the
cloud-primary router isn't active.
"""

from __future__ import annotations

import json

from tools.registry import tool

_router = None


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
