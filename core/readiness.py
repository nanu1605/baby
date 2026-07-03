"""Startup readiness sequence, shared by CLI and UI (spec Section 13).

Never announce ready when Baby can't actually respond: the model must be
reachable and warm before any ready signal fires.
"""

from __future__ import annotations

import time

from core.providers.ollama import OllamaProvider
from db.database import Database


async def ready_check(provider: OllamaProvider, db: Database) -> tuple[bool, list[str]]:
    """Run the readiness sequence. Returns (ok, human-readable notes).

    Steps: provider reachable → 1-token warm-up ping (loads the model into
    VRAM) → served-context verification. DB is assumed connected by caller.
    """
    notes: list[str] = []

    if not await provider.healthy():
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
