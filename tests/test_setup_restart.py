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

The same shape bit voice. The pipeline loads at boot, and on a first install the
wake-word models do not exist yet -- the wizard fetches them minutes later. The log
says so in order: "voice unavailable: NoSuchFile ... hey_jarvis_v0.1.onnx", then
"Baby ready (text only)", and only THEN the openWakeWord downloads. Nothing
re-attaches voice, so a fresh install stayed deaf for its whole first session.

So the backend now asks to be restarted, and the shell -- which spawned it and is
already watching the handle -- brings it straight back against the machine the
wizard has finished setting up. The contract is a single magic number crossing a
language boundary, which is what most of these tests are about:

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


def test_a_first_install_whose_voice_died_for_want_of_models_restarts(monkeypatch):
    """Voice loads at boot; the wizard downloads its models minutes later.

    Straight off a real first-run log, in this order:
        voice: voice unavailable: NoSuchFile ... hey_jarvis_v0.1.onnx ... doesn't exist
        Baby ready (text only) -- voice failed to load
        <openWakeWord downloads start here>
    Nothing re-attaches voice afterwards, so a fresh install was deaf for its entire
    first session -- on a build whose headline feature is a wake word. No cloud key
    is involved, so the router check alone would let this through.
    """
    monkeypatch.setenv("BABY_SHELL_TRAY", "1")
    assert server._restart_needed_to_apply({}, object(), voice_failed=True) is True
    assert (
        server._restart_needed_to_apply(
            {"router_mode": "local_primary"}, object(), voice_failed=True
        )
        is True
    )


def test_working_voice_is_not_bounced(monkeypatch):
    """Voice that loaded is not a reason to restart anything."""
    monkeypatch.setenv("BABY_SHELL_TRAY", "1")
    assert (
        server._restart_needed_to_apply(
            {"router_mode": "local_primary"}, object(), voice_failed=False
        )
        is False
    )


def test_an_always_on_service_is_not_restarted_for_voice_either(monkeypatch):
    """The ownership gate holds for every reason, not only the router one."""
    monkeypatch.delenv("BABY_SHELL_TRAY", raising=False)
    assert server._restart_needed_to_apply({}, object(), voice_failed=True) is False


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


def _client_with_ctx(tmp_path, monkeypatch, *, voice_failed: bool):
    """Like tests.test_keys._client, but hands back the ctx so the voice flag the
    endpoint reads can actually be set."""
    import asyncio as _asyncio

    from fastapi.testclient import TestClient

    from core.agent import AgentCore
    from core.bus import EventBus
    from core.safety import SafetyConfig, SafetyGate
    from db.database import Database
    from tests.conftest import FakeProvider
    from ui.server import UIContext, create_app

    monkeypatch.setenv("BABY_HOME", str(tmp_path))
    db = Database(tmp_path / "s.db")

    async def _boot():
        await db.connect()
        return await db.create_conversation("ui")

    conv = _asyncio.run(_boot())
    bus = EventBus()
    gate = SafetyGate(SafetyConfig(mode="dry_run"), bus)
    agent = AgentCore(FakeProvider([]), db, conv, channel="ui", bus=bus, gate=gate)
    ctx = UIContext(db=db, bus=bus, gate=gate, agent=agent, config={})
    ctx.voice_failed = voice_failed
    return TestClient(create_app(ctx)), db


def test_finishing_the_wizard_after_voice_died_arms_the_restart(tmp_path, monkeypatch):
    """End to end: a Full install, no cloud key, voice dead at boot."""
    monkeypatch.setenv("BABY_SHELL_TRAY", "1")
    client, db = _client_with_ctx(tmp_path, monkeypatch, voice_failed=True)
    try:
        paths.write_setup({"install_mode": "full", "router_mode": "local_primary"})
        body = client.post("/api/setup/complete", json={"acknowledged": True}).json()
        assert body["restarting"] is True
        assert client.app.state.restart_event.is_set()
    finally:
        _close(db)


def test_the_boot_records_whether_voice_came_up():
    """`ctx.voice is None` also means "never asked for", so the endpoint cannot read
    the failure off it. run_ui has to record the outcome explicitly."""
    src = (_ROOT / "ui" / "server.py").read_text(encoding="utf-8")
    assert "ctx.voice_failed = not voice_ok" in src, "the failure is never recorded"
    assert "voice_failed=ctx.voice_failed" in src, "the endpoint never reads it"


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


# --- a key added AFTER the wizard has to apply itself ------------------------
# Reported from a real install: the wizard was finished without ever pressing
# Save (Test proves the key and stores nothing), so `.env` was empty and
# router_mode unstamped. The repair panel could not add one -- it listed the keys
# read-only and said to hand-edit .env, which stamps nothing either. And even a
# correct hand edit changed nothing until the next launch, because the router is
# built once at boot. So the save endpoint now applies itself.


def _keys_client(tmp_path, monkeypatch, *, complete: bool, tray: bool):
    from core import keys as keymod

    for spec in keymod.KEYS:
        monkeypatch.delenv(spec.env, raising=False)
    if tray:
        monkeypatch.setenv("BABY_SHELL_TRAY", "1")
    else:
        monkeypatch.delenv("BABY_SHELL_TRAY", raising=False)
    client, db = _client_with_ctx(tmp_path, monkeypatch, voice_failed=False)
    paths.write_setup({"install_mode": "full", "setup_complete": complete})
    return client, db


def _save(client, monkeypatch):
    from tests.test_keys import _probe

    _probe(monkeypatch, status=200)
    return client.post(
        "/api/setup/keys", json={"env": "OPENROUTER_API_KEY", "key": _FAKE_KEY}
    ).json()


def test_a_key_added_after_setup_applies_itself(tmp_path, monkeypatch):
    """The repair-panel case: no wizard behind it to restart the process, and a
    provider built at boot from a key state that had no key in it."""
    client, db = _keys_client(tmp_path, monkeypatch, complete=True, tray=True)
    try:
        body = _save(client, monkeypatch)
        assert body["saved"] is True
        assert body["router_mode"] == "cloud_primary"
        assert body["restarting"] is True
        assert body["restart_required"] is False  # nothing for the user to do
        assert client.app.state.restart_requested is True
        assert client.app.state.restart_event.is_set()
        assert _FAKE_KEY not in str(body)
    finally:
        _close(db)


def test_a_key_saved_during_the_wizard_does_not_bounce_it(tmp_path, monkeypatch):
    """Restarting here would tear the user's own screen away several steps before
    they are done with it. /api/setup/complete owns that restart."""
    client, db = _keys_client(tmp_path, monkeypatch, complete=False, tray=True)
    try:
        body = _save(client, monkeypatch)
        assert body["saved"] is True
        assert body["restarting"] is False
        assert client.app.state.restart_requested is False
        assert not client.app.state.restart_event.is_set()
    finally:
        _close(db)


def test_a_key_save_never_restarts_a_backend_the_shell_does_not_own(tmp_path, monkeypatch):
    """An attached service or a bare dev run has nobody to bring it back, so the
    honest answer is to tell the user to reopen it."""
    client, db = _keys_client(tmp_path, monkeypatch, complete=True, tray=False)
    try:
        body = _save(client, monkeypatch)
        assert body["restarting"] is False
        assert body["restart_required"] is True
        assert not client.app.state.restart_event.is_set()
    finally:
        _close(db)


# --- the two screens that hand a key over -----------------------------------
# vitest covers the pure decisions in lib/setup.ts; these pin the JSX facts it
# cannot reach -- that the guard is actually wired to the button, and that the
# repair panel really does take a key now.

_APP = _ROOT / "ui" / "app" / "src"


def test_continue_cannot_walk_past_a_typed_but_unsaved_key():
    """Test proves a key and stores nothing. Continue used to look only at the
    server's can_finish, which a Full install always satisfies -- so a tested key
    was dropped on the way to the next step, silently."""
    src = (_APP / "components" / "FirstRunWizard.tsx").read_text(encoding="utf-8")
    assert "canLeaveKeysStep(keys, pending)" in src, "the button ignores unsaved keys"
    assert "onPendingChange={notePending}" in src, "nothing reports a field as pending"
    assert "canLeaveKeys(keys)" not in src, "the old unguarded check is still wired up"


def test_the_repair_panel_can_add_a_key_not_just_list_them():
    """Finishing setup used to be the last moment a key could be added: the panel
    was read-only and pointed at a text editor, which also leaves router_mode
    unstamped, so even a correct hand edit left Baby on the local brain."""
    src = (_APP / "components" / "RepairPanel.tsx").read_text(encoding="utf-8")
    assert 'import { KeyField }' in src, "the panel has no way to enter a key"
    assert "<KeyField" in src
    assert "edit" not in src.split("<h3>API keys</h3>", 1)[1].split("</section>", 1)[0], (
        "the panel still tells the user to hand-edit .env"
    )


def test_one_key_field_serves_both_screens():
    """Two copies drift: the wizard's would get the guard and the panel's would
    quietly keep the old behaviour."""
    assert (_APP / "components" / "KeyField.tsx").is_file()
    wizard = (_APP / "components" / "FirstRunWizard.tsx").read_text(encoding="utf-8")
    assert "function KeyField(" not in wizard, "the wizard still defines its own"
