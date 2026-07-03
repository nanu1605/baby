"""Tool package. register_all() imports every tool module, which registers
each @tool with the registry as a side effect."""

from __future__ import annotations


def register_all() -> None:
    from tools import apps, clock, files, memory_tools, shell, system_stats, web  # noqa: F401
