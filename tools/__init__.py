"""Tool package. register_all() imports every tool module, which registers
each @tool with the registry as a side effect."""

from __future__ import annotations


def register_all() -> None:
    from tools import (  # noqa: F401
        apps,
        browser,
        clock,
        files,
        game,
        memory_tools,
        projects,
        screen,
        shell,
        system_stats,
        tasks,
        web,
    )
