"""v6 W2: first-run GPU pre-check + install-mode fork + setup-state persistence.

The wizard state lives in BABY_HOME/setup.json (never by rewriting config.yaml),
is overlaid non-destructively at load, and drives the Full-vs-cloud-only fork. A
missing setup.json is a no-op so a dev checkout is unchanged.
"""

from __future__ import annotations

import asyncio
import json
import time

import pytest
from fastapi.testclient import TestClient

from core import paths
from core.agent import AgentCore
from core.bus import EventBus
from core.safety import SafetyConfig, SafetyGate
from db.database import Database
from tests.conftest import FakeProvider
from ui import server
from ui.server import UIContext, create_app

_CONFIG = {"models": {"daily": {"provider": "ollama", "model": "m"}}}


# --- setup.json state (core/paths.py) -----------------------------------------


def test_read_setup_absent(monkeypatch, tmp_path):
    monkeypatch.setenv("BABY_HOME", str(tmp_path))
    assert paths.read_setup() == {}
    assert paths.is_setup_complete() is False


def test_write_setup_merges(monkeypatch, tmp_path):
    monkeypatch.setenv("BABY_HOME", str(tmp_path))
    paths.write_setup({"install_mode": "cloud_only"})
    paths.write_setup({"setup_complete": True})  # merge, don't replace
    state = paths.read_setup()
    assert state["install_mode"] == "cloud_only"
    assert state["setup_complete"] is True
    assert paths.is_setup_complete() is True


def test_apply_setup_overlays_router_mode(monkeypatch, tmp_path):
    monkeypatch.setenv("BABY_HOME", str(tmp_path))
    paths.write_setup({"router_mode": "cloud_primary"})
    cfg = {"router": {"mode": "local_primary"}}
    paths.apply_setup(cfg)
    assert cfg["router"]["mode"] == "cloud_primary"


def test_apply_setup_noop_without_file(monkeypatch, tmp_path):
    monkeypatch.setenv("BABY_HOME", str(tmp_path))  # empty, no setup.json
    cfg = {"router": {"mode": "local_primary"}}
    paths.apply_setup(cfg)
    assert cfg["router"]["mode"] == "local_primary"  # dev/pre-wizard: unchanged


def test_read_setup_corrupt_never_raises(monkeypatch, tmp_path):
    monkeypatch.setenv("BABY_HOME", str(tmp_path))
    paths.setup_path().write_text("{ not json", encoding="utf-8")
    assert paths.read_setup() == {}


def test_is_installed_tracks_baby_home(monkeypatch, tmp_path):
    monkeypatch.delenv("BABY_HOME", raising=False)
    assert paths.is_installed() is False  # dev checkout: wizard never shows
    monkeypatch.setenv("BABY_HOME", str(tmp_path))
    assert paths.is_installed() is True


# --- GPU recommendation (ui/server.py) ----------------------------------------


def test_gpu_recommendation_no_gpu(monkeypatch):
    monkeypatch.setattr("tools.system_stats._gpu", lambda: None)
    r = server._gpu_recommendation()
    assert r["has_nvidia"] is False
    assert r["recommend"] == "cloud_only"
    assert r["meets_full_bar"] is False


def test_gpu_recommendation_meets_bar(monkeypatch):
    monkeypatch.setattr(
        "tools.system_stats._gpu",
        lambda: {"name": "RTX 4070", "util_percent": 0, "vram_used_gb": 1.0, "vram_total_gb": 12.0},
    )
    r = server._gpu_recommendation()
    assert r["meets_full_bar"] is True
    assert r["recommend"] == "full"
    assert r["vram_total_gb"] == 12.0
    assert r["gpu_name"] == "RTX 4070"


def test_gpu_recommendation_below_bar(monkeypatch):
    monkeypatch.setattr(
        "tools.system_stats._gpu",
        lambda: {"name": "GTX 1650", "util_percent": 0, "vram_used_gb": 0.5, "vram_total_gb": 4.0},
    )
    r = server._gpu_recommendation()
    assert r["meets_full_bar"] is False
    assert r["recommend"] == "cloud_only"


# --- endpoints ----------------------------------------------------------------


def _client(tmp_path, monkeypatch):
    monkeypatch.setenv("BABY_HOME", str(tmp_path))  # setup.json -> tmp, not the repo
    db = Database(tmp_path / "s.db")
    conv = asyncio.run(_boot(db))
    bus = EventBus()
    gate = SafetyGate(SafetyConfig(mode="dry_run"), bus)
    agent = AgentCore(FakeProvider([]), db, conv, channel="ui", bus=bus, gate=gate)
    ctx = UIContext(db=db, bus=bus, gate=gate, agent=agent, config=_CONFIG)
    return TestClient(create_app(ctx)), db


async def _boot(db: Database) -> int:
    await db.connect()
    return await db.create_conversation("ui")


def test_setup_mode_endpoint_writes_and_validates(tmp_path, monkeypatch):
    client, db = _client(tmp_path, monkeypatch)
    try:
        ok = client.post("/api/setup/mode", json={"mode": "cloud_only"})
        assert ok.status_code == 200
        assert ok.json()["install_mode"] == "cloud_only"
        assert paths.read_setup()["install_mode"] == "cloud_only"

        bad = client.post("/api/setup/mode", json={"mode": "bogus"})
        assert bad.status_code == 400
    finally:
        asyncio.run(db.close())


def test_setup_gpu_endpoint(tmp_path, monkeypatch):
    monkeypatch.setattr("tools.system_stats._gpu", lambda: None)
    client, db = _client(tmp_path, monkeypatch)
    try:
        r = client.get("/api/setup/gpu")
        assert r.status_code == 200
        body = r.json()
        assert body["recommend"] == "cloud_only"
        assert body["full_bar_gb"] == server._FULL_MODE_MIN_VRAM_GB
    finally:
        asyncio.run(db.close())


def test_stats_exposes_setup_state(tmp_path, monkeypatch):
    client, db = _client(tmp_path, monkeypatch)
    try:
        paths.write_setup({"install_mode": "full"})
        stats = client.get("/stats").json()
        assert stats["setup"]["install_mode"] == "full"
        assert stats["setup"]["complete"] is False
        # _client sets BABY_HOME (installed layout) -> the wizard-gate signal is true.
        assert stats["setup"]["installed"] is True
    finally:
        asyncio.run(db.close())


# --- a failed download must reach the user as the message we wrote -----------
# Reported from a clean VM with the network pulled mid-provision: the wizard
# showed "EventBus.publish() got multiple values for argument 'kind'" as the
# provisioning failure. `classify_error` returns a dict with a `kind` key and the
# route splats it into `bus.publish("setup_progress", "setup", **ev)`, so the
# payload key bound to publish's own parameter. The code reporting the failure
# was the code that failed, and a raw TypeError replaced the retryable message
# W3 wrote specifically so a stranger never sees a Python trace.


def test_a_classified_step_failure_surfaces_its_message_not_a_traceback(tmp_path, monkeypatch):
    from core import paths, provision

    client, db = _client(tmp_path, monkeypatch)
    try:
        paths.write_setup({"install_mode": "cloud_only"})

        async def failing_provision(mode, *, on_event, browser=False):
            # Exactly what the real walk emits when a download cannot connect.
            on_event(provision._event(
                "kokoro", "error", status="error",
                **provision.classify_error("getaddrinfo failed")
            ))
            return {"ok": False}

        monkeypatch.setattr(provision, "provision", failing_provision)
        assert client.post("/api/setup/provision").json()["status"] == "started"

        for _ in range(200):
            if not client.get("/api/setup/status").json()["provisioning"]:
                break
        else:
            pytest.fail("provisioning never finished")

        progress = client.get("/api/setup/status").json()["progress"]
        kokoro = progress["kokoro"]
        assert kokoro["status"] == "error"
        assert kokoro["kind"] == "no_network"
        assert kokoro["retryable"] is True
        assert "Couldn't reach the download server" in kokoro["message"]

        # The failure path must not itself fail: no step may carry a Python error.
        blob = json.dumps(progress)
        for leak in ("TypeError", "Traceback", "multiple values for argument"):
            assert leak not in blob, f"{leak} leaked into the wizard: {blob}"
    finally:
        asyncio.run(db.close())


# --- start Baby with Windows (v6.0.2) -----------------------------------------
# The endpoint half. core/autostart.py is covered in tests/test_autostart.py; what
# matters here is that the route refuses rather than guesses when there is no
# installed exe to point a Run value at, and that it reports the REGISTRY's answer
# rather than echoing the request back.


def _stub_autostart(monkeypatch, *, supported=True, start=False):
    """Replace the registry with a single bool, and record the calls."""
    import ui.server as server

    state = {"enabled": start, "calls": []}
    monkeypatch.setattr(server.autostart, "supported", lambda: supported)
    monkeypatch.setattr(server.autostart, "enabled", lambda: state["enabled"])

    def _enable(exe):
        state["calls"].append(("enable", exe))
        state["enabled"] = True
        return True

    def _disable():
        state["calls"].append(("disable", None))
        state["enabled"] = False
        return True

    monkeypatch.setattr(server.autostart, "enable", _enable)
    monkeypatch.setattr(server.autostart, "disable", _disable)
    return state


def test_autostart_endpoint_round_trips(tmp_path, monkeypatch):
    monkeypatch.setenv("BABY_SHELL_EXE", r"C:\Users\x\AppData\Local\Programs\Baby\Baby.exe")
    state = _stub_autostart(monkeypatch)
    client, db = _client(tmp_path, monkeypatch)
    try:
        on = client.post("/api/setup/autostart", json={"enabled": True})
        assert on.status_code == 200
        assert on.json()["enabled"] is True
        assert state["calls"][-1][0] == "enable"

        off = client.post("/api/setup/autostart", json={"enabled": False})
        assert off.json()["enabled"] is False
        assert state["calls"][-1][0] == "disable"

        # And /stats reports it, which is how the toggle knows where it stands.
        assert client.get("/stats").json()["autostart"] == {
            "supported": True,
            "enabled": False,
        }
    finally:
        asyncio.run(db.close())


def test_autostart_is_refused_without_an_installed_exe(tmp_path, monkeypatch):
    """A source checkout is started by a developer typing a command. Writing a Run
    value that points at a guessed install path would fail silently every boot."""
    monkeypatch.delenv("BABY_SHELL_EXE", raising=False)
    state = _stub_autostart(monkeypatch)
    client, db = _client(tmp_path, monkeypatch)
    try:
        r = client.post("/api/setup/autostart", json={"enabled": True})
        assert r.status_code == 400
        assert state["calls"] == [], "it tried to write anyway"
        assert client.get("/stats").json()["autostart"]["supported"] is False
    finally:
        asyncio.run(db.close())


def test_autostart_requires_the_enabled_field(tmp_path, monkeypatch):
    monkeypatch.setenv("BABY_SHELL_EXE", r"C:\Baby\Baby.exe")
    state = _stub_autostart(monkeypatch)
    client, db = _client(tmp_path, monkeypatch)
    try:
        # A missing field must not be read as "off" -- that would silently turn a
        # user's autostart off on a malformed request.
        assert client.post("/api/setup/autostart", json={}).status_code == 400
        assert state["calls"] == []
    finally:
        asyncio.run(db.close())


def test_autostart_reports_the_registry_not_the_request(tmp_path, monkeypatch):
    """If the write is blocked -- policy, or a locked-down machine -- the answer
    must be what the registry says, not what was asked for."""
    monkeypatch.setenv("BABY_SHELL_EXE", r"C:\Baby\Baby.exe")
    import ui.server as server

    monkeypatch.setattr(server.autostart, "supported", lambda: True)
    monkeypatch.setattr(server.autostart, "enabled", lambda: False)  # never takes
    monkeypatch.setattr(server.autostart, "enable", lambda exe: False)
    client, db = _client(tmp_path, monkeypatch)
    try:
        r = client.post("/api/setup/autostart", json={"enabled": True})
        assert r.status_code == 200
        assert r.json()["enabled"] is False, "it claimed success the registry denies"
    finally:
        asyncio.run(db.close())


# --- the provision row carried a raw library string (v6.0.2) ------------------


def test_a_walk_ending_outside_a_dep_step_still_gets_a_classified_row(tmp_path, monkeypatch):
    """The endpoint used to write str(exc) straight onto the `provision` row, so an
    exception raised where no dep step was running -- the final re-verify is the
    clear case -- reached the user as raw library text. Observed:
    "Cannot send a request, as the client has been closed.", which is httpx telling
    huggingface_hub off and means nothing to anyone installing an assistant."""
    import ui.server as server
    from core import provision as provmod

    client, db = _client(tmp_path, monkeypatch)
    try:
        paths.write_setup({"install_mode": "cloud_only"})

        async def boom(mode, *, on_event):
            raise RuntimeError("Cannot send a request, as the client has been closed.")

        monkeypatch.setattr(provmod, "provision", boom)
        assert client.post("/api/setup/provision").status_code == 200

        for _ in range(200):
            row = server_progress(server, client).get("provision")
            if row:
                break
            time.sleep(0.02)
        row = server_progress(server, client)["provision"]

        assert row["status"] == "error"
        assert row.get("kind") == "stale_client", "the row was never classified"
        assert "reopen" in row["message"].lower()
        # The raw text stays available for a diagnostics paste, but it is not what
        # a reader should show.
        assert row["message"] != row["detail"]
        assert "client has been closed" not in row["message"]
    finally:
        asyncio.run(db.close())


def server_progress(server, client) -> dict:
    return client.get("/api/setup/status").json().get("progress", {})
