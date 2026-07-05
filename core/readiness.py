"""Startup readiness sequence, shared by CLI and UI (spec Section 13).

Never announce ready when Baby can't actually respond: the model must be
reachable and warm before any ready signal fires. At logon Baby usually wins
the race against the Ollama app (observed live: the autostart task died 20 s
after login with "Ollama is not reachable"), so an unreachable daemon gets a
background `ollama serve` attempt plus a polling window before giving up.
"""

from __future__ import annotations

import asyncio
import os
import shutil
import subprocess
import time
from pathlib import Path

from core.providers.ollama import OllamaProvider
from db.database import Database


def _try_start_ollama(notes: list[str]) -> None:
    """Best-effort background `ollama serve` (the daemon may simply not be up yet)."""
    exe = shutil.which("ollama")
    if not exe:
        guess = Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Ollama" / "ollama.exe"
        exe = str(guess) if guess.exists() else ""
    if not exe:
        return
    try:
        subprocess.Popen(
            [exe, "serve"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        notes.append("started `ollama serve` in the background")
    except Exception:  # noqa: BLE001 — a failed spawn just means we keep polling
        pass


async def _wait_for_provider(provider: OllamaProvider, wait_s: int, notes: list[str]) -> bool:
    if await provider.healthy():
        return True
    if wait_s <= 0:
        return False
    notes.append(f"Ollama not reachable yet - waiting up to {wait_s}s for it to come up...")
    _try_start_ollama(notes)
    deadline = time.monotonic() + wait_s
    started = time.monotonic()
    while time.monotonic() < deadline:
        await asyncio.sleep(3)
        if await provider.healthy():
            notes.append(f"Ollama up after {time.monotonic() - started:.0f}s")
            return True
    return False


async def ready_check(
    provider: OllamaProvider, db: Database, *, wait_s: int = 120
) -> tuple[bool, list[str]]:
    """Run the readiness sequence. Returns (ok, human-readable notes).

    Steps: provider reachable (with a wait_s grace window for the logon race)
    → 1-token warm-up ping (loads the model into VRAM) → served-context
    verification. DB is assumed connected by caller.
    """
    notes: list[str] = []

    if not await _wait_for_provider(provider, wait_s, notes):
        notes.append("Baby could not start: Ollama is not reachable at 127.0.0.1:11434.")
        notes.append("Start it with:  ollama serve   (or launch the Ollama app)")
        return False, notes

    started = time.monotonic()
    notes.append("warming up model...")
    try:
        async for _ in provider.chat([{"role": "user", "content": "ping"}], max_tokens=1):
            pass
    except Exception as exc:  # noqa: BLE001 — readiness must report, not crash
        notes.append(f"Baby could not start: model warm-up failed: {exc}")
        return False, notes
    notes.append(f"model warm in {time.monotonic() - started:.1f}s")

    ctx = await provider.loaded_context_length()
    if ctx is not None and ctx < provider.num_ctx:
        notes.append(
            f"warning: Ollama is serving a {ctx}-token context but config wants "
            f"{provider.num_ctx}. Set OLLAMA_CONTEXT_LENGTH={provider.num_ctx} and "
            "restart Ollama (scripts/setup.ps1 does both)."
        )
    return True, notes
