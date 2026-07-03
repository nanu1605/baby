"""Gated PowerShell execution. Classification happens in the agent BEFORE
dispatch reaches this tool — the tool itself stays dumb and just runs."""

from __future__ import annotations

import asyncio
from pathlib import Path

from tools.registry import tool

_MAX_OUTPUT = 8 * 1024  # combined stdout+stderr budget (spec Section 10)
# PS 5.1 defaults native output to the ANSI code page; force UTF-8 so Hindi
# filenames and unicode output survive (DECISIONS.md #9).
_UTF8_PRELUDE = "[Console]::OutputEncoding=[System.Text.Encoding]::UTF8; "


@tool
async def run_shell(command: str, cwd: str = "~", timeout_s: int = 60) -> dict:
    """Run a PowerShell command and capture its output."""
    workdir = Path(cwd).expanduser()
    if not workdir.is_dir():
        return {"error": f"cwd does not exist: {workdir}"}
    timeout_s = max(1, min(int(timeout_s), 300))

    proc = await asyncio.create_subprocess_exec(
        "powershell.exe",
        "-NoProfile",
        "-NonInteractive",
        "-Command",
        _UTF8_PRELUDE + command,
        cwd=str(workdir),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        out_b, err_b = await asyncio.wait_for(proc.communicate(), timeout_s)
    except TimeoutError:
        proc.kill()
        await proc.wait()
        return {"error": f"timed out after {timeout_s}s (process killed)"}

    out = out_b.decode("utf-8", errors="replace")
    err = err_b.decode("utf-8", errors="replace")
    truncated = False
    if len(out) + len(err) > _MAX_OUTPUT:
        truncated = True
        out = out[: _MAX_OUTPUT - min(len(err), 2048)]
        err = err[:2048]
    return {
        "exit_code": proc.returncode,
        "stdout": out,
        "stderr": err,
        "truncated": truncated,
    }
