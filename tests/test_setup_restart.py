r"""The wizard stamps a router mode the running process cannot honour.

Found on a real install, three minutes after the wizard closed. `setup.json` said
`router_mode: cloud_primary`, the OpenRouter key in `.env` was live and answering
chat completions, and `/stats` carried no `router`, no `game_mode` and no
`brain_turns` at all -- because `build_provider` had run once, at boot, against a
config read BEFORE the wizard existed, and had returned a bare OllamaProvider.

Two features read as broken as a result. The cloud badge never lit. The game-mode
button POSTed to a provider with no `set_game_mode` and silently did nothing, while
Ollama held the 9B in VRAM the whole time. The only thing in the product that knew
better was one line of text on the wizard's last screen.

So the backend now asks to be restarted, and the shell -- which spawned it and is
already watching the handle -- brings it straight back on the stamped mode. The
contract is a single magic number crossing a language boundary, which is what most
of these tests are about:

  * `_restart_needed_to_apply` fires ONLY when a restart would actually change
    something, and never for a backend the shell does not own.
  * `RESTART_EXIT_CODE` means the same thing in Python and in Rust.
  * `run.py` propagates it, and the shell branches on it instead of reporting a
    crash.
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

from core import paths
from core.router import CloudRouter, build_provider
from tests.test_keys import _FAKE_KEY, _client, _close
from ui import server

_ROOT = Path(__file__).resolve().parent.parent
_MAIN_RS = _ROOT / "ui" / "shell" / "src-tauri" / "src" / "main.rs"


def _template() -> dict:
    return yaml.safe_load(paths._TEMPLATE.read_text(encoding="utf-8"))


# --- when a restart is worth asking for -------------------------------------


def test_a_stamped_cloud_mode_this_process_cannot_honour_asks_for_a_restart(monkeypatch):
    """The exact live state: cloud_primary stamped, provider built before the stamp."""
    monkeypatch.setenv("BABY_SHELL_TRAY", "1")
    stale = object()  # anything that is not a CloudRouter
    assert server._restart_needed_to_apply({"router_mode": "cloud_primary"}, stale) is True


def test_a_process_already_on_the_stamped_mode_is_left_alone(monkeypatch):
    """Restarting a backend that already honours the mode would be a pointless
    outage -- and, on a repeated wizard visit, a loop."""
    monkeypatch.setenv("BABY_SHELL_TRAY", "1")
    monkeypatch.setenv("OPENROUTER_API_KEY", _FAKE_KEY)
    cfg = _template()
    cfg["router"]["mode"] = "cloud_primary"
    provider = build_provider(cfg)
    assert isinstance(provider, CloudRouter), "test premise: this build is cloud-primary"
    assert server._restart_needed_to_apply({"router_mode": "cloud_primary"}, provider) is False


def test_a_local_only_install_is_not_restarted(monkeypatch):
    """Nothing to apply: the mode the wizard stamped is the one already running."""
    monkeypatch.setenv("BABY_SHELL_TRAY", "1")
    assert server._restart_needed_to_apply({"router_mode": "local_primary"}, object()) is False
    assert server._restart_needed_to_apply({}, object()) is False


def test_an_always_on_service_is_never_restarted_by_the_wizard(monkeypatch):
    """Only the shell can restart a backend, and only one it spawned. An autostart
    service (or a dev run in a terminal) has nobody watching to bring it back, so
    exiting would take Baby down for good -- the opposite of the fix."""
    monkeypatch.delenv("BABY_SHELL_TRAY", raising=False)
    assert server._restart_needed_to_apply({"router_mode": "cloud_primary"}, object()) is False


# --- the endpoint has to act on it ------------------------------------------


def test_finishing_the_wizard_arms_the_restart(tmp_path, monkeypatch):
    monkeypatch.setenv("BABY_SHELL_TRAY", "1")
    client, db = _client(tmp_path, monkeypatch)
    try:
        paths.write_setup({"install_mode": "full", "router_mode": "cloud_primary"})
        body = client.post("/api/setup/complete", json={"acknowledged": True}).json()
        assert body["restarting"] is True
        app = client.app
        assert app.state.restart_requested is True
        assert app.state.restart_event.is_set(), "run_ui would never stop uvicorn"
    finally:
        _close(db)


def test_finishing_without_a_mode_change_arms_nothing(tmp_path, monkeypatch):
    """A Full install with no cloud key must not bounce the backend for nothing."""
    monkeypatch.setenv("BABY_SHELL_TRAY", "1")
    client, db = _client(tmp_path, monkeypatch)
    try:
        paths.write_setup({"install_mode": "full", "router_mode": "local_primary"})
        body = client.post("/api/setup/complete", json={"acknowledged": True}).json()
        assert body["restarting"] is False
        assert client.app.state.restart_requested is False
        assert not client.app.state.restart_event.is_set()
    finally:
        _close(db)


# --- the number has to mean the same thing on both sides --------------------


def test_the_restart_exit_code_matches_the_shell():
    """Python picks the exit status; Rust decides what it means. Nothing but this
    test connects the two, and a mismatch degrades silently into the old bug: the
    shell would read a deliberate restart as a crash and tell the user to reopen."""
    rs = _MAIN_RS.read_text(encoding="utf-8")
    m = re.search(r"const RESTART_EXIT_CODE: i32 = (\d+);", rs)
    assert m, "the shell no longer defines RESTART_EXIT_CODE"
    assert int(m.group(1)) == server.RESTART_EXIT_CODE


def test_the_server_stops_itself_and_reports_the_code():
    """Arming the flag achieves nothing on its own: uvicorn has to be told to stop,
    or the process never exits and there is nothing for the shell to restart. And the
    exit status is the only channel back -- run_ui has to return it."""
    src = (_ROOT / "ui" / "server.py").read_text(encoding="utf-8")
    watch = src.split("async def _restart_watch(", 1)[1].split("\n    restart_task", 1)[0]
    assert "app.state.restart_event.wait()" in watch
    assert "server.should_exit = True" in watch
    assert "return RESTART_EXIT_CODE if app.state.restart_requested else 0" in src


def test_the_entrypoint_hands_the_exit_code_to_the_shell():
    """run_ui returning the code is useless if run.py drops it on the floor."""
    src = (_ROOT / "run.py").read_text(encoding="utf-8")
    assert "code = asyncio.run(run_ui(" in src
    assert "sys.exit(code)" in src


def test_the_shell_restarts_instead_of_reporting_a_crash():
    src = _MAIN_RS.read_text(encoding="utf-8")
    watcher = src.split("fn watch_backend(", 1)[1].split("\n/// Bring the backend", 1)[0]
    assert "RESTART_EXIT_CODE" in watcher, "the watcher treats every exit as a death"
    assert "restart_backend(" in watcher

    restart = src.split("fn restart_backend(", 1)[1].split("\n/// ", 1)[0]
    # A message alone is what the old passive hint already was. It has to respawn,
    # wait for the port, and put the user back on the live UI.
    assert "spawn_backend(" in restart
    assert "wait_ready(" in restart
    assert "reveal(" in restart
    # And a restart that fails still has to say so rather than hanging on the overlay.
    assert "show_backend_died(" in restart
