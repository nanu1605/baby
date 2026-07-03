"""Everything SDK (voidtools) ctypes wrapper — IPC to a running Everything.exe.

Not a registered tool; tools/files.py calls search() and falls back to the
scandir index when this returns None (DLL missing, app not running, error).
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import os
from datetime import datetime, timedelta
from pathlib import Path

_REQUEST_NAME = 0x00000001
_REQUEST_PATH = 0x00000002
_REQUEST_SIZE = 0x00000010
_REQUEST_DATE_MODIFIED = 0x00000040
_ERROR_IPC = 2

_dll: ctypes.WinDLL | None = None
_dll_missing = False


def _dll_candidates() -> list[Path]:
    local = os.environ.get("LOCALAPPDATA", "")
    return [
        Path(local) / "baby" / "Everything64.dll" if local else None,
        Path(__file__).resolve().parent.parent / "assets" / "Everything64.dll",
    ]


def _load() -> ctypes.WinDLL | None:
    global _dll, _dll_missing
    if _dll is not None or _dll_missing:
        return _dll
    for candidate in [c for c in _dll_candidates() if c] + ["Everything64.dll"]:
        try:
            _dll = ctypes.WinDLL(str(candidate))
            break
        except OSError:
            continue
    if _dll is None:
        _dll_missing = True
        return None
    _dll.Everything_GetResultFullPathNameW.argtypes = [wt.DWORD, wt.LPWSTR, wt.DWORD]
    return _dll


def _filetime_to_iso(ft: int) -> str:
    # FILETIME: 100ns intervals since 1601-01-01.
    try:
        stamp = datetime(1601, 1, 1) + timedelta(microseconds=ft // 10)
        return stamp.isoformat(timespec="seconds")
    except (OverflowError, OSError):
        return ""


def available() -> bool:
    dll = _load()
    if dll is None:
        return False
    # A trivial query tells us whether Everything.exe is answering IPC.
    dll.Everything_SetSearchW("")
    dll.Everything_SetMax(1)
    if not dll.Everything_QueryW(True):
        return dll.Everything_GetLastError() != _ERROR_IPC
    return True


def search(query: str, max_results: int) -> list[dict] | None:
    """Instant filename search. None → caller should use the fallback index."""
    dll = _load()
    if dll is None:
        return None
    try:
        dll.Everything_SetSearchW(query)
        dll.Everything_SetRequestFlags(
            _REQUEST_NAME | _REQUEST_PATH | _REQUEST_SIZE | _REQUEST_DATE_MODIFIED
        )
        dll.Everything_SetMax(max_results)
        if not dll.Everything_QueryW(True):
            return None
        count = dll.Everything_GetNumResults()
        results: list[dict] = []
        buffer = ctypes.create_unicode_buffer(32768)
        for i in range(count):
            dll.Everything_GetResultFullPathNameW(i, buffer, len(buffer))
            size = ctypes.c_longlong(0)
            modified = ctypes.c_longlong(0)
            dll.Everything_GetResultSize(i, ctypes.byref(size))
            dll.Everything_GetResultDateModified(i, ctypes.byref(modified))
            results.append(
                {
                    "path": buffer.value,
                    "size": size.value,
                    "modified": _filetime_to_iso(modified.value),
                }
            )
        return results
    except (OSError, AttributeError):
        return None
