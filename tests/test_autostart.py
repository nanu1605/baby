"""Start Baby with Windows (v6.0.2).

Nothing shipped did this. `scripts/autostart.ps1` registers Task Scheduler entries
but is hardcoded to a dev checkout and `scripts/` is not in the installer payload at
all, so on an installed machine a reboot meant launching Baby by hand.

The tests run against a fake `winreg` rather than the real registry: a test suite
that writes a real Run key would make the developer's own machine start Baby at
logon, which is both rude and a good way to never notice a bug in `disable()`.
"""

from __future__ import annotations

import re
import sys
import types
from pathlib import Path

import pytest

from core import autostart

_NSH = Path(__file__).resolve().parents[1] / "ui" / "shell" / "src-tauri" / "installer_hooks.nsh"
_RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"


class _FakeKey:
    def __init__(self, store: dict, path: str):
        self.store = store
        self.path = path

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _fake_winreg(monkeypatch, *, existing: dict | None = None, readonly: bool = False):
    """A winreg stand-in over a dict. Returns the dict, so a test can assert on the
    exact bytes that would have been written."""
    values: dict = dict(existing or {})
    mod = types.ModuleType("winreg")
    mod.HKEY_CURRENT_USER = "HKCU"
    mod.KEY_READ = 1
    mod.KEY_SET_VALUE = 2
    mod.REG_SZ = 1

    def _open(root, path, _res, access):
        if path != _RUN_KEY:
            raise OSError("wrong key")
        if access == mod.KEY_READ and _RUN_KEY not in values:
            raise FileNotFoundError(_RUN_KEY)
        return _FakeKey(values, path)

    def _create(root, path, _res, access):
        if readonly:
            raise PermissionError("policy says no")
        values.setdefault(_RUN_KEY, {})
        return _FakeKey(values, path)

    def _query(key, name):
        bucket = values.get(key.path, {})
        if name not in bucket:
            raise FileNotFoundError(name)
        return bucket[name], mod.REG_SZ

    def _set(key, name, _res, _typ, data):
        values.setdefault(key.path, {})[name] = data

    def _delete(key, name):
        bucket = values.get(key.path, {})
        if name not in bucket:
            raise FileNotFoundError(name)
        del bucket[name]

    mod.OpenKey = _open
    mod.CreateKeyEx = _create
    mod.QueryValueEx = _query
    mod.SetValueEx = _set
    mod.DeleteValue = _delete
    monkeypatch.setitem(sys.modules, "winreg", mod)
    monkeypatch.setattr(autostart, "supported", lambda: True)
    return values


@pytest.fixture
def exe(tmp_path) -> Path:
    """A path with a space in it, because %LOCALAPPDATA%\\Programs is one directory
    away from `C:\\Users\\Anna Maria\\...` and an unquoted path breaks there."""
    d = tmp_path / "Program Files" / "Baby"
    d.mkdir(parents=True)
    p = d / "Baby.exe"
    p.write_bytes(b"MZ")
    return p


def test_enable_writes_a_quoted_path_and_the_minimized_flag(monkeypatch, exe):
    values = _fake_winreg(monkeypatch)
    assert autostart.enable(exe) is True
    written = values[_RUN_KEY]["Baby"]

    assert written == f'"{exe}" --minimized'
    # The quotes are the whole point on a path containing a space.
    assert written.startswith('"') and f'{exe}"' in written
    # NOT --attach-only: that flag means "never spawn a backend, wait for the
    # always-on service", and at logon there is no such service -- Baby would wait
    # for something that never arrives and then report it did not come up.
    assert "--attach-only" not in written
    assert autostart.enabled() is True


def test_disable_removes_it_and_is_happy_when_it_was_never_there(monkeypatch, exe):
    values = _fake_winreg(monkeypatch)
    autostart.enable(exe)
    assert autostart.disable() is True
    assert "Baby" not in values.get(_RUN_KEY, {})
    assert autostart.enabled() is False
    # Asking for "off" when it is already off is success, not an error.
    assert autostart.disable() is True


def test_enable_is_idempotent(monkeypatch, exe):
    values = _fake_winreg(monkeypatch)
    autostart.enable(exe)
    autostart.enable(exe)
    assert list(values[_RUN_KEY]) == ["Baby"], "a second enable duplicated the entry"


def test_enable_refuses_an_exe_that_is_not_there(monkeypatch, tmp_path):
    """A Run value pointing at nothing fails silently every boot, with nothing to
    tell the user why. Better to refuse and say so."""
    values = _fake_winreg(monkeypatch)
    assert autostart.enable(tmp_path / "nope" / "Baby.exe") is False
    assert values.get(_RUN_KEY, {}) == {}


def test_a_refused_write_reports_failure_rather_than_claiming_success(monkeypatch, exe):
    _fake_winreg(monkeypatch, readonly=True)
    assert autostart.enable(exe) is False
    assert autostart.enabled() is False


def test_enabled_reads_the_registry_rather_than_remembering(monkeypatch, exe):
    """The user can turn Baby off from Task Manager's Startup tab. If this returned
    a cached flag, the toggle in the repair panel would report the opposite of what
    the machine actually does."""
    values = _fake_winreg(monkeypatch)
    autostart.enable(exe)
    assert autostart.enabled() is True
    del values[_RUN_KEY]["Baby"]  # as Task Manager would
    assert autostart.enabled() is False


def test_state_is_not_offered_without_an_installed_exe(monkeypatch, exe):
    """BABY_SHELL_EXE is set only by the native shell. A source checkout is started
    by a developer typing a command, and has no exe to point a Run value at."""
    _fake_winreg(monkeypatch)
    assert autostart.state(None)["supported"] is False
    assert autostart.state(exe)["supported"] is True


def test_it_is_a_no_op_off_windows(monkeypatch, exe):
    monkeypatch.setattr(autostart, "supported", lambda: False)
    assert autostart.enable(exe) is False
    assert autostart.enabled() is False
    assert autostart.disable() is True  # "not registered" is the requested state
    assert autostart.state(exe)["supported"] is False


# --- the uninstaller has to take it with them ---------------------------------


def test_the_uninstaller_removes_the_run_value():
    nsh = _NSH.read_text(encoding="utf-8")
    assert "DeleteRegValue HKCU" in nsh and '"Baby"' in nsh, (
        "uninstalling Baby would leave a Run value that tries to launch a deleted "
        "exe at every logon"
    )
    assert _RUN_KEY in nsh


def test_the_run_value_delete_is_not_gated_on_the_delete_data_checkbox():
    """It is not user data. Someone who unticks "delete application data" is asking
    to keep their conversations, not to keep a startup entry for an app they just
    removed -- and that entry is the one leftover they cannot trace back to Baby.
    """
    nsh = _NSH.read_text(encoding="utf-8")
    delete_at = nsh.index("DeleteRegValue HKCU")
    guard_at = nsh.index("$DeleteAppDataCheckboxState = 1")
    assert delete_at < guard_at, (
        "the Run-value delete sits inside the delete-app-data guard, so an "
        "uninstall that keeps user data leaves Baby launching itself forever"
    )


def test_the_real_uninstall_guards_still_stand():
    """The Run delete must not have loosened what protects a user's data during an
    upgrade -- that branch ate real keys and history twice already."""
    nsh = _NSH.read_text(encoding="utf-8")
    assert "$UpdateMode <> 1" in nsh
    assert re.search(r"\$R4\s*!=\s*\$R5", nsh), "the reinstall guard is gone"
    assert 'IfFileExists "$LOCALAPPDATA\\baby\\.venv\\*.*"' in nsh, (
        "the dev-checkout guard on RmDir is gone"
    )
