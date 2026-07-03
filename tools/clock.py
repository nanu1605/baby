"""Phase 0 dummy tool — proves the tool-calling loop end to end."""

from __future__ import annotations

from datetime import datetime

from tools.registry import tool


@tool
def get_time() -> str:
    """Current local date and time."""
    return datetime.now().strftime("%A, %d %B %Y, %H:%M:%S")
