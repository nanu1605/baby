"""Global push-to-talk hotkey via Win32 RegisterHotKey.

ctypes + a message pump on a daemon thread: no admin rights, no third-party
keyboard hooks, atomic combo detection, clean unregister. If the combo is
already taken by another app, start() returns False and voice continues
wake-word-only (spec: PTT is the reliable fallback, not the only path).
"""

from __future__ import annotations

import threading
from collections.abc import Callable

MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
MOD_SHIFT = 0x0004
MOD_WIN = 0x0008
MOD_NOREPEAT = 0x4000
WM_HOTKEY = 0x0312
WM_QUIT = 0x0012

_MODS = {"ctrl": MOD_CONTROL, "alt": MOD_ALT, "shift": MOD_SHIFT, "win": MOD_WIN}


def _parse_combo(combo: str) -> tuple[int, int]:
    """'ctrl+alt+b' -> (modifier mask, virtual-key code)."""
    mods = 0
    vk = 0
    for part in combo.lower().split("+"):
        part = part.strip()
        if part in _MODS:
            mods |= _MODS[part]
        elif len(part) == 1:
            vk = ord(part.upper())
        else:
            raise ValueError(f"unsupported key in hotkey combo: {part!r}")
    if not vk:
        raise ValueError(f"hotkey combo has no key: {combo!r}")
    return mods | MOD_NOREPEAT, vk


class PushToTalk:
    _HOTKEY_ID = 0xBAB1

    def __init__(self, combo: str, on_press: Callable[[], None]) -> None:
        self.combo = combo
        self.on_press = on_press
        self._thread: threading.Thread | None = None
        self._thread_id: int | None = None
        self._registered = threading.Event()
        self._failed = threading.Event()

    def start(self) -> bool:
        """Register + pump on a daemon thread; False if the combo is taken."""
        self._thread = threading.Thread(target=self._pump, name="baby-ptt", daemon=True)
        self._thread.start()
        self._registered.wait(timeout=2.0)
        return self._registered.is_set() and not self._failed.is_set()

    def _pump(self) -> None:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32
        self._thread_id = kernel32.GetCurrentThreadId()
        try:
            mods, vk = _parse_combo(self.combo)
        except ValueError:
            self._failed.set()
            self._registered.set()
            return
        if not user32.RegisterHotKey(None, self._HOTKEY_ID, mods, vk):
            self._failed.set()
            self._registered.set()
            return
        self._registered.set()
        try:
            msg = wintypes.MSG()
            while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
                if msg.message == WM_HOTKEY and msg.wParam == self._HOTKEY_ID:
                    try:
                        self.on_press()
                    except Exception:  # noqa: BLE001 — callback bugs must not kill the pump
                        pass
        finally:
            user32.UnregisterHotKey(None, self._HOTKEY_ID)

    def stop(self) -> None:
        if self._thread_id is not None:
            import ctypes

            ctypes.windll.user32.PostThreadMessageW(self._thread_id, WM_QUIT, 0, 0)
        self._thread_id = None
