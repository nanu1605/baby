"""V1 tray reconciliation: the backend must skip its pystray tray only when the
v4 native shell owns it (ui.shell: native). Pure config read — no pystray, no loop."""

from __future__ import annotations

from ui.server import _shell_owns_tray


def test_default_config_backend_owns_tray():
    # No ui block at all -> browser default -> backend keeps its own tray.
    assert _shell_owns_tray({}) is False
    assert _shell_owns_tray({"ui": {}}) is False


def test_browser_shell_backend_owns_tray():
    assert _shell_owns_tray({"ui": {"shell": "browser"}}) is False


def test_native_shell_defers_to_shell():
    assert _shell_owns_tray({"ui": {"shell": "native"}}) is True


def test_native_is_case_and_space_insensitive():
    assert _shell_owns_tray({"ui": {"shell": " Native "}}) is True
    assert _shell_owns_tray({"ui": {"shell": "NATIVE"}}) is True


def test_unknown_value_backend_keeps_tray():
    # Any non-native value is treated as "not native" -> backend keeps its tray.
    assert _shell_owns_tray({"ui": {"shell": "electron"}}) is False
