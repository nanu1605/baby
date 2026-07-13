"""v6 W2: first-run GPU pre-check + install-mode fork + setup-state persistence.

The wizard state lives in BABY_HOME/setup.json (never by rewriting config.yaml),
is overlaid non-destructively at load, and drives the Full-vs-cloud-only fork. A
missing setup.json is a no-op so a dev checkout is unchanged.
"""

from __future__ import annotations

import asyncio

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
