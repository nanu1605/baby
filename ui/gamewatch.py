"""Optional game-mode auto-detect: fullscreen app in the foreground toggles
game mode on; leaving it toggles back off (NIM plan §2.5, `game_mode.auto_detect`,
default false).

Only reverses what it caused: a manual "game mode on" is never fought by the
watcher when the owner alt-tabs out.
"""

from __future__ import annotations

import asyncio

_SHELL_CLASSES = {"workerw", "progman", "shell_traywnd"}  # desktop itself is "fullscreen"


def covers_monitor(win: tuple[int, int, int, int], mon: tuple[int, int, int, int]) -> bool:
    """True when the window rect fully covers the monitor rect (pure, tested)."""
    wl, wt, wr, wb = win
    ml, mt, mr, mb = mon
    return wl <= ml and wt <= mt and wr >= mr and wb >= mb


def _fullscreen_now() -> bool:
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    hwnd = user32.GetForegroundWindow()
    if not hwnd:
        return False
    cls = ctypes.create_unicode_buffer(64)
    user32.GetClassNameW(hwnd, cls, 64)
    if cls.value.lower() in _SHELL_CLASSES:
        return False
    rect = wintypes.RECT()
    if not user32.GetWindowRect(hwnd, ctypes.byref(rect)):
        return False
    monitor = user32.MonitorFromWindow(hwnd, 2)  # MONITOR_DEFAULTTONEAREST

    class MONITORINFO(ctypes.Structure):
        _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", wintypes.RECT),
                    ("rcWork", wintypes.RECT), ("dwFlags", wintypes.DWORD)]

    info = MONITORINFO()
    info.cbSize = ctypes.sizeof(MONITORINFO)
    if not user32.GetMonitorInfoW(monitor, ctypes.byref(info)):
        return False
    m = info.rcMonitor
    return covers_monitor(
        (rect.left, rect.top, rect.right, rect.bottom),
        (m.left, m.top, m.right, m.bottom),
    )


class GameWatch:
    """Polls the foreground window; drives router.set_game_mode."""

    def __init__(self, provider, poll_s: float = 10.0) -> None:
        self.provider = provider
        self.poll_s = poll_s
        self._auto_set = False  # we only turn OFF what we turned ON
        self._task: asyncio.Task | None = None

    def start(self) -> None:
        self._task = asyncio.create_task(self._run(), name="baby-gamewatch")

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass

    async def _run(self) -> None:
        while True:
            await asyncio.sleep(self.poll_s)
            try:
                fullscreen = await asyncio.to_thread(_fullscreen_now)
                if fullscreen and not self.provider.game_mode:
                    await self.provider.set_game_mode(True)
                    self._auto_set = True
                elif not fullscreen and self.provider.game_mode and self._auto_set:
                    await self.provider.set_game_mode(False)
                    self._auto_set = False
            except Exception:  # noqa: BLE001 — the watcher must never die
                pass
