"""Start Baby with Windows, or don't.

Nothing shipped did this. `scripts/autostart.ps1` registers Task Scheduler entries
but is hardcoded to a dev checkout (`<repo>\\.venv\\Scripts\\pythonw.exe`) and
`scripts/` is not in the installer payload at all -- see `scripts/stage_payload.ps1`,
which enumerates what ships. So on an installed machine Baby only ever started when
someone clicked it, and a reboot meant launching it by hand.

An `HKCU\\...\\Run` value, for three reasons. It needs no admin, which keeps the
installer's no-admin promise. It is one `winreg` call in the module and hive
`core/provision.py` already writes `OLLAMA_CONTEXT_LENGTH` to, so it adds no
dependency. And it is trivially removable -- by us on uninstall, and by the user in
any of the several places Windows lists startup apps.

The registry is the ONLY source of truth here. There is a mirror of nothing: a flag
in setup.json would drift the moment the user turned Baby off from Task Manager's
Startup tab, and then the toggle in the repair panel would lie about the state of
their machine.
"""

from __future__ import annotations

import shlex
import sys
from pathlib import Path

# The Run key is per-user; the machine-wide one under HKLM needs admin and would
# start Baby for every account on the box, neither of which is wanted.
_RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
_VALUE = "Baby"
# Not --attach-only, which means "never spawn a backend, wait for the always-on
# service". At logon there is no such service, so that flag would leave Baby
# waiting for something that never arrives. --minimized spawns normally and only
# skips revealing the window.
_ARG = "--minimized"


def supported() -> bool:
    """False anywhere there is no HKCU to write to. The toggle hides itself."""
    return sys.platform == "win32"


def command(exe: str | Path) -> str:
    """The exact string that goes in the Run value.

    The path is quoted because `%LOCALAPPDATA%\\Programs\\Baby\\Baby.exe` is one
    unquoted space away from Windows trying to run `C:\\Program`, and an installed
    path under a user folder like `C:\\Users\\Anna Maria\\...` hits that for real.
    """
    return f'"{Path(exe)}" {_ARG}'


def enabled() -> bool:
    """Whether Baby is set to start with Windows, read from the registry itself."""
    if not supported():
        return False
    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _RUN_KEY, 0, winreg.KEY_READ) as key:
            value, _ = winreg.QueryValueEx(key, _VALUE)
    except OSError:
        return False
    return bool(str(value).strip())


def target() -> str:
    """The command currently registered, or "" -- for diagnostics and tests."""
    if not supported():
        return ""
    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _RUN_KEY, 0, winreg.KEY_READ) as key:
            value, _ = winreg.QueryValueEx(key, _VALUE)
    except OSError:
        return ""
    return str(value)


def enable(exe: str | Path) -> bool:
    """Register Baby to start at logon. True if the registry now says so.

    Refuses an exe that is not on disk rather than writing a Run value that points
    at nothing: a startup entry that fails silently every boot is worse than no
    startup entry, because nothing ever tells the user why.
    """
    if not supported():
        return False
    exe = Path(exe)
    if not exe.is_file():
        return False
    import winreg

    try:
        with winreg.CreateKeyEx(
            winreg.HKEY_CURRENT_USER, _RUN_KEY, 0, winreg.KEY_SET_VALUE
        ) as key:
            winreg.SetValueEx(key, _VALUE, 0, winreg.REG_SZ, command(exe))
    except OSError:
        return False
    return True


def disable() -> bool:
    """Remove the entry. True once it is gone, including when it never existed."""
    if not supported():
        return True
    import winreg

    try:
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER, _RUN_KEY, 0, winreg.KEY_SET_VALUE
        ) as key:
            winreg.DeleteValue(key, _VALUE)
    except FileNotFoundError:
        return True  # already absent is the state we were asked for
    except OSError:
        return False
    return True


def state(exe: str | Path | None) -> dict:
    """What /stats reports: whether the toggle can be offered, and its position.

    `exe` is BABY_SHELL_EXE, which only the native shell sets. Without it there is
    nothing to point a Run value at -- a source checkout is started by a developer
    typing a command, not by a shortcut -- so the toggle is not offered.
    """
    return {
        "supported": supported() and bool(exe),
        "enabled": enabled(),
    }


def describe(exe: str | Path) -> str:
    """Human-readable form of what would be registered. Used in diagnostics."""
    return shlex.join([str(Path(exe)), _ARG])
