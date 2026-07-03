"""App control: open/close/focus/list Windows applications.

Open resolves through a Start-Menu shortcut index built once per boot
(WScript.Shell COM via one PowerShell subprocess — no pywin32, no binary
.lnk parsing). Close is graceful WM_CLOSE first, hard kill after 5 s.
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import difflib
import json
import os
import subprocess
import time
from pathlib import Path

from core.safety import SYSTEM_PROCESSES
from tools.registry import tool

CACHE_DIR = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "baby"
_WM_CLOSE = 0x0010
_SW_RESTORE = 9

# Fallback aliases for apps the model commonly names.
_ALIASES = {
    "chrome": "chrome",
    "google chrome": "chrome",
    "edge": "msedge",
    "msedge": "msedge",
    "notepad": "notepad",
    "explorer": "explorer",
    "file explorer": "explorer",
    "spotify": "spotify",
    "vlc": "vlc",
    "word": "winword",
    "excel": "excel",
    "powerpoint": "powerpnt",
    "calculator": "calc",
    "calc": "calc",
    "terminal": "wt",
    "vs code": "code",
    "vscode": "code",
    "code": "code",
}

_index: list[dict] | None = None

_INDEX_SCRIPT = r"""
$sh = New-Object -ComObject WScript.Shell
$dirs = @("$env:ProgramData\Microsoft\Windows\Start Menu\Programs",
          "$env:APPDATA\Microsoft\Windows\Start Menu\Programs")
$out = foreach ($d in $dirs) {
  if (Test-Path $d) {
    Get-ChildItem $d -Recurse -Filter *.lnk | ForEach-Object {
      try {
        $t = $sh.CreateShortcut($_.FullName).TargetPath
        $exe = if ($t) { [IO.Path]::GetFileNameWithoutExtension($t) } else { $null }
        [pscustomobject]@{ name = $_.BaseName; lnk = $_.FullName; exe = $exe }
      } catch {}
    }
  }
}
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$out | ConvertTo-Json -Compress
"""


def build_index() -> list[dict]:
    """Build (or load cached) Start-Menu shortcut index. Called at startup."""
    global _index
    cache = CACHE_DIR / "app_index.json"
    if cache.exists() and (time.time() - cache.stat().st_mtime) < 86400:
        try:
            _index = json.loads(cache.read_text(encoding="utf-8"))
            return _index
        except (OSError, ValueError):
            pass
    try:
        proc = subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", _INDEX_SCRIPT],
            capture_output=True,
            timeout=30,
        )
        data = json.loads(proc.stdout.decode("utf-8", errors="replace") or "[]")
        _index = data if isinstance(data, list) else [data]
    except (OSError, ValueError, subprocess.TimeoutExpired):
        _index = []
    try:
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps(_index), encoding="utf-8")
    except OSError:
        pass
    return _index


def _find_shortcut(name: str) -> dict | None:
    index = _index if _index is not None else build_index()
    needle = name.lower().strip()
    by_name = {e["name"].lower(): e for e in index if e.get("name")}
    if needle in by_name:
        return by_name[needle]
    for key, entry in by_name.items():
        if key.startswith(needle):
            return entry
    for key, entry in by_name.items():
        if needle in key:
            return entry
    close = difflib.get_close_matches(needle, list(by_name), n=1, cutoff=0.75)
    return by_name[close[0]] if close else None


def _target_procnames(name: str) -> list[str]:
    """Process names (no .exe) that `name` may refer to."""
    needle = name.lower().strip().removesuffix(".exe")
    names = {needle}
    if needle in _ALIASES:
        names.add(_ALIASES[needle])
    entry = _find_shortcut(needle)
    if entry and entry.get("exe"):
        names.add(entry["exe"].lower())
    return list(names)


def _windows_for_pids(pids: set[int]) -> list[tuple[int, int, str]]:
    """(hwnd, pid, title) for visible titled top-level windows of pids."""
    user32 = ctypes.windll.user32
    results: list[tuple[int, int, str]] = []

    @ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)
    def enum_cb(hwnd, _lparam):
        if not user32.IsWindowVisible(hwnd):
            return True
        length = user32.GetWindowTextLengthW(hwnd)
        if length == 0:
            return True
        pid = wt.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if not pids or pid.value in pids:
            buf = ctypes.create_unicode_buffer(length + 1)
            user32.GetWindowTextW(hwnd, buf, length + 1)
            results.append((hwnd, pid.value, buf.value))
        return True

    user32.EnumWindows(enum_cb, 0)
    return results


def _pids_by_procnames(names: list[str]) -> set[int]:
    import psutil

    wanted = {n.lower() for n in names}
    pids = set()
    for p in psutil.process_iter(["name", "pid"]):
        pname = (p.info["name"] or "").lower().removesuffix(".exe")
        if pname in wanted:
            pids.add(p.info["pid"])
    return pids


@tool
def app_control(action: str, name: str = "") -> dict:
    """Open, close, focus or list apps on this PC."""
    action = (action or "").lower()
    if action == "list":
        windows = _windows_for_pids(set())
        import psutil

        out, seen = [], set()
        for _, pid, title in windows:
            if pid in seen:
                continue
            seen.add(pid)
            try:
                pname = psutil.Process(pid).name()
            except psutil.Error:
                pname = "?"
            out.append({"name": pname, "pid": pid, "title": title[:80]})
        return {"apps": out}

    if not name:
        return {"error": f"'{action}' needs an app name"}

    if action == "open":
        entry = _find_shortcut(name)
        if entry:
            os.startfile(entry["lnk"])  # noqa: S606 — deliberate app launch
            return {"opened": entry["name"], "via": "start-menu"}
        target = _ALIASES.get(name.lower().strip(), name)
        try:
            os.startfile(target)  # noqa: S606
            return {"opened": target, "via": "path"}
        except OSError:
            return {"error": f"could not find an app matching {name!r}"}

    if action in ("close", "focus"):
        procnames = _target_procnames(name)
        # Belt and braces: the gate already denies these, never trust one layer.
        if any(n in SYSTEM_PROCESSES for n in procnames):
            return {"error": f"refusing to touch critical system process: {name}"}
        pids = _pids_by_procnames(procnames)
        if not pids:
            return {"error": f"no running process matches {name!r}"}
        windows = _windows_for_pids(pids)

        if action == "focus":
            if not windows:
                return {"error": f"{name} has no visible window"}
            user32 = ctypes.windll.user32
            hwnd = windows[0][0]
            user32.ShowWindow(hwnd, _SW_RESTORE)
            if not user32.SetForegroundWindow(hwnd):
                return {"error": "could not take foreground focus (Windows policy)"}
            return {"focused": name, "title": windows[0][2]}

        # close: graceful WM_CLOSE, then hard kill survivors after 5 s.
        user32 = ctypes.windll.user32
        for hwnd, _, _ in windows:
            user32.PostMessageW(hwnd, _WM_CLOSE, 0, 0)
        import psutil

        deadline = time.time() + 5
        while time.time() < deadline:
            if not any(psutil.pid_exists(pid) for pid in pids):
                return {"closed": name, "graceful": True}
            time.sleep(0.25)
        killed = []
        for pid in pids:
            try:
                psutil.Process(pid).kill()
                killed.append(pid)
            except psutil.Error:
                continue
        return {"closed": name, "graceful": False, "killed_pids": killed}

    return {"error": f"unknown action: {action} (use open|close|focus|list)"}
