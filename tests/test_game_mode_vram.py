r"""Game mode promises a free GPU. The flag alone does not deliver one.

Reported from a real first run, with a screenshot: the header showed game mode
ON and the cloud badge lit, next to a VRAM bar reading 8.0 of 9 GB. Both were
telling the truth. `run_ui` sets `provider.game_mode = True` at boot and
deliberately skips `set_game_mode` -- whose comment says it "would try to unload
a model that was never warmed".

On a first run that assumption is exactly backwards. The wizard's last act is the
functional re-verify, and `health.check_ollama_model` warm-pings the 9B on
purpose ("1 token loads the weights into VRAM" -- its own comment). The backend
then restarts into game mode on top of a model Ollama is already holding, and
nothing evicts it. Measured on the reporting machine: 1841 MiB before the probe,
7785 MiB after, with `/api/ps` showing qwen3.5:9b-q4_K_M resident at 5.31 GB and
`/stats` cheerfully reporting `game_mode: true`. One `unload()` freed it in 2.2s.

The same probe runs behind the repair panel's "Run a check", so pressing it
mid-game cost 5 GB until Ollama's keep_alive expired.
"""

from __future__ import annotations

import asyncio

import pytest

from tests.conftest import FakeProvider
from ui import server


class _Daily(FakeProvider):
    """A local provider that counts evictions, like the router's own tests use."""

    def __init__(self, *, fail: bool = False, hang: bool = False):
        super().__init__([])
        self.unloads = 0
        self._fail = fail
        self._hang = hang

    async def unload(self):
        self.unloads += 1
        if self._fail:
            raise RuntimeError("ollama said no")
        if self._hang:
            await asyncio.sleep(3600)


class _Router:
    """Stand-in for CloudRouter: a game_mode flag over a local `daily`."""

    def __init__(self, *, game_mode: bool, daily=None):
        self.game_mode = game_mode
        if daily is not None:
            self.daily = daily


# --- the eviction itself -----------------------------------------------------


@pytest.mark.asyncio
async def test_game_mode_on_hands_the_vram_back():
    daily = _Daily()
    assert await server._evict_local_brain(_Router(game_mode=True, daily=daily)) is True
    assert daily.unloads == 1


@pytest.mark.asyncio
async def test_nothing_is_evicted_when_game_mode_is_off():
    """Not in game mode, the local brain is supposed to be resident."""
    daily = _Daily()
    assert await server._evict_local_brain(_Router(game_mode=False, daily=daily)) is False
    assert daily.unloads == 0


@pytest.mark.asyncio
async def test_a_provider_with_no_local_brain_is_not_an_error():
    """A cloud-only install's provider has no `daily` to unload, and a bare
    OllamaProvider has no `game_mode` at all."""
    assert await server._evict_local_brain(_Router(game_mode=True)) is False
    assert await server._evict_local_brain(object()) is False


@pytest.mark.asyncio
async def test_a_failing_unload_never_takes_the_boot_down():
    daily = _Daily(fail=True)
    assert await server._evict_local_brain(_Router(game_mode=True, daily=daily)) is False
    assert daily.unloads == 1  # attempted, and swallowed


@pytest.mark.asyncio
async def test_a_hung_unload_does_not_hold_the_boot(monkeypatch):
    """Freeing VRAM is best-effort; a wedged Ollama must not stall the launch."""
    monkeypatch.setattr(server, "_EVICT_TIMEOUT_S", 0.05)
    daily = _Daily(hang=True)
    assert await server._evict_local_brain(_Router(game_mode=True, daily=daily)) is False


# --- the three places a model gets loaded behind game mode's back ------------


def _client(tmp_path, monkeypatch, provider):
    """A real app whose ctx.agent.provider is the one under test."""
    from fastapi.testclient import TestClient

    from core.agent import AgentCore
    from core.bus import EventBus
    from core.safety import SafetyConfig, SafetyGate
    from db.database import Database
    from ui.server import UIContext, create_app

    monkeypatch.setenv("BABY_HOME", str(tmp_path))
    db = Database(tmp_path / "s.db")

    async def _boot():
        await db.connect()
        return await db.create_conversation("ui")

    conv = asyncio.run(_boot())
    bus = EventBus()
    gate = SafetyGate(SafetyConfig(mode="dry_run"), bus)
    agent = AgentCore(provider, db, conv, channel="ui", bus=bus, gate=gate)
    ctx = UIContext(db=db, bus=bus, gate=gate, agent=agent, config={})
    return TestClient(create_app(ctx)), db


def _close(db):
    asyncio.run(db.close())


def test_run_a_check_gives_the_vram_back(tmp_path, monkeypatch):
    """health.run_all loads the 9B to prove it answers. In game mode that is a
    loan, not a purchase."""
    from core import health, paths

    daily = _Daily()
    router = _Router(game_mode=True, daily=daily)
    client, db = _client(tmp_path, monkeypatch, router)
    try:
        paths.write_setup({"install_mode": "full"})
        warm = health.Result("ollama-model", True, True, "pass", "warm")
        monkeypatch.setattr(health, "run_all", lambda mode, level, browser: [warm])
        r = client.get("/api/setup/health")
        assert r.status_code == 200
        assert daily.unloads == 1
    finally:
        _close(db)


def test_provisioning_gives_the_vram_back_when_it_finishes(tmp_path, monkeypatch):
    """provision() ends on the same functional re-verify -- which is the exact run
    that left the reporting machine at 8.0/9 GB with game mode on."""
    from core import paths, provision

    daily = _Daily()
    router = _Router(game_mode=True, daily=daily)
    client, db = _client(tmp_path, monkeypatch, router)
    try:
        paths.write_setup({"install_mode": "full"})

        async def fake_provision(mode, *, on_event, browser=False):
            on_event({"dep": "verify", "phase": "check", "status": "pass"})
            return {"ok": True}

        monkeypatch.setattr(provision, "provision", fake_provision)
        assert client.post("/api/setup/provision").json()["status"] == "started"
        for _ in range(200):
            if not client.get("/api/setup/status").json()["provisioning"]:
                break
        else:
            pytest.fail("provisioning never finished, so the eviction proves nothing")
        assert daily.unloads == 1
    finally:
        _close(db)


def test_the_boot_evicts_instead_of_only_setting_the_flag():
    """run_ui is not reachable from a unit test, so pin the line. Setting
    `game_mode = True` and stopping there is the bug that was reported."""
    from pathlib import Path

    src = (Path(__file__).resolve().parent.parent / "ui" / "server.py").read_text(
        encoding="utf-8"
    )
    boot = src.split('if cloud_mode and hasattr(provider, "game_mode"):', 1)[1]
    boot = boot.split("gamewatch = None", 1)[0]
    assert "provider.game_mode = True" in boot
    assert "_evict_local_brain(provider)" in boot, "the flag is set but the GPU is not freed"
