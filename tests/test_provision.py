"""v6 W3: first-run provisioning orchestrator. Tests cover the pure logic
(failure classifier, byte-progress math, /api/pull line parsing, URL decode, disk
pre-check) and the orchestrator WALK with mocked primitives -- the real multi-GB
downloads + Ollama pull run on a provisioned box (dev smoke) and the owner's
clean-VM matrix, not in unit tests."""

from __future__ import annotations

import asyncio

from fastapi.testclient import TestClient

from core import paths, provision
from core.agent import AgentCore
from core.bus import EventBus
from core.safety import SafetyConfig, SafetyGate
from db.database import Database
from tests.conftest import FakeProvider
from ui.server import UIContext, create_app

_CONFIG = {"models": {"daily": {"provider": "ollama", "model": "m"}}}


# --- failure classifier -----------------------------------------------------


def test_classify_error_buckets():
    assert provision.classify_error("407 proxy tunnel required")["kind"] == "proxy"
    assert provision.classify_error("dns error: No such host")["kind"] == "no_network"
    assert provision.classify_error("error sending request for url")["kind"] == "no_network"
    assert provision.classify_error("No space left on device")["kind"] == "disk_full"
    assert provision.classify_error("hash mismatch on wheel")["kind"] == "corrupt"
    assert provision.classify_error("weird thing")["kind"] == "unknown"
    # Every classification is retryable and carries a plain message (never a trace).
    for txt in ("proxy", "timeout", "ENOSPC", "corrupt", ""):
        c = provision.classify_error(txt)
        assert c["retryable"] is True and c["message"] and "Traceback" not in c["message"]


def test_bare_connect_is_no_network_not_proxy():
    # The W0 regression: uv/reqwest says "client error (Connect)" for plain DNS fails.
    assert provision.classify_error("client error (Connect)")["kind"] == "no_network"


# --- byte math + pull parsing -----------------------------------------------


def test_rate_math():
    r = provision._rate(done=50, total=100, elapsed_s=2.0, moved=50)
    assert r["pct"] == 50.0
    assert r["speed_bps"] == 25  # 50 bytes / 2 s
    assert r["eta_s"] == 2  # remaining 50 / 25 bps
    # No total (indeterminate) must not divide by zero.
    assert provision._rate(10, 0, 1.0, 10)["pct"] == 0.0


def test_pull_progress_only_emits_on_byte_lines():
    starts: dict = {}
    byte_line = {"status": "pulling", "digest": "sha256:x", "total": 100, "completed": 40}
    ev = provision._pull_progress(byte_line, starts, now=10.0)
    assert ev is not None and ev["dep"] == "ollama-model" and ev["bytes_total"] == 100
    # A non-byte phase line (manifest/verifying) yields nothing.
    assert provision._pull_progress({"status": "verifying sha256"}, starts, now=11.0) is None


def test_plan_matches_mode_and_walk_order():
    cloud = [s["key"] for s in provision.plan("cloud_only")]
    full = [s["key"] for s in provision.plan("full")]
    # Both bookended by disk + verify; Full inserts the local-brain steps before verify.
    assert cloud[0] == "disk" and cloud[-1] == "verify"
    assert "ollama-model" not in cloud
    assert full.index("ollama-model") < full.index("verify")
    assert full.index("ollama-daemon") < full.index("ollama-model")
    # Speaker is the one optional step; sizes come through for the byte-heavy models.
    speaker = next(s for s in provision.plan("cloud_only") if s["key"] == "speaker")
    assert speaker["required"] is False
    whisper = next(s for s in provision.plan("cloud_only") if s["key"] == "whisper")
    assert whisper["size_mb"] > 0


def test_dest_name_decodes_percent_encoding():
    # The CAM++ asset url encodes '+' as %2B; on disk it must be the literal '++'.
    url = "https://x/wespeaker_en_voxceleb_CAM%2B%2B.onnx"
    assert provision._dest_name(url) == "wespeaker_en_voxceleb_CAM++.onnx"


def test_check_disk_flags_insufficient_space(monkeypatch, tmp_path):
    import shutil as _sh

    class U:
        def __init__(self, free):
            self.free = free

    monkeypatch.setattr(_sh, "disk_usage", lambda p: U(500 * 1024 * 1024))  # 500 MB free
    d = provision.check_disk("full", path=tmp_path)
    assert d["ok"] is False and d["free_mb"] == 500 and d["need_mb"] > 500

    monkeypatch.setattr(_sh, "disk_usage", lambda p: U(50 * 1024 * 1024 * 1024))  # 50 GB
    assert provision.check_disk("full", path=tmp_path)["ok"] is True


# --- orchestrator walk (mocked primitives) ----------------------------------


def _mock_primitives(monkeypatch, *, ollama_up=True):
    calls: dict = {"downloads": [], "pulls": 0, "owakeword": 0, "whisper": 0, "embedder": 0}

    async def fake_download(url, dest, *, on_event, dep, retries=3):
        calls["downloads"].append(dep)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(b"x")
        on_event(provision._event(dep, "download", status="done", detail="fake"))

    async def fake_pull(model, *, on_event, host=provision._OLLAMA, retries=3):
        calls["pulls"] += 1
        on_event(provision._event("ollama-model", "download", status="done", detail=model))

    async def fake_healthy(host=provision._OLLAMA):
        return ollama_up

    def _bump(key):
        return lambda: calls.__setitem__(key, 1)

    monkeypatch.setattr(provision, "download_file", fake_download)
    monkeypatch.setattr(provision, "pull_ollama_model", fake_pull)
    monkeypatch.setattr(provision, "_ollama_healthy", fake_healthy)
    monkeypatch.setattr(provision, "_download_openwakeword", _bump("owakeword"))
    monkeypatch.setattr(provision, "_download_whisper", _bump("whisper"))
    monkeypatch.setattr(provision, "_download_embedder", _bump("embedder"))
    monkeypatch.setattr(
        provision, "check_disk", lambda *a, **k: {"ok": True, "free_mb": 99999, "need_mb": 1}
    )
    monkeypatch.setattr(provision.health, "check_vcredist", lambda: ("pass", "ok"))
    monkeypatch.setattr(
        provision.health, "run_all",
        lambda mode, level, browser: [provision.health.Result("x", True, True, "pass", "ok")],
    )
    return calls


def test_provision_cloud_only_skips_ollama(monkeypatch, tmp_path):
    monkeypatch.setenv("BABY_HOME", str(tmp_path))
    calls = _mock_primitives(monkeypatch)
    events: list = []
    result = asyncio.run(provision.provision("cloud_only", on_event=events.append))
    assert result["ok"] is True
    assert calls["pulls"] == 0  # no local brain in cloud-only
    assert "kokoro" in calls["downloads"] and calls["owakeword"] == 1
    assert calls["whisper"] == 1 and calls["embedder"] == 1
    # Re-verify ran and setup was marked provisioned.
    assert any(e["dep"] == "verify" and e["status"] == "pass" for e in events)
    assert paths.read_setup().get("provisioned") is True


def test_provision_full_pulls_the_9b(monkeypatch, tmp_path):
    monkeypatch.setenv("BABY_HOME", str(tmp_path))
    calls = _mock_primitives(monkeypatch, ollama_up=True)
    asyncio.run(provision.provision("full", on_event=lambda e: None))
    assert calls["pulls"] == 1


def test_provision_full_reports_missing_daemon(monkeypatch, tmp_path):
    monkeypatch.setenv("BABY_HOME", str(tmp_path))
    calls = _mock_primitives(monkeypatch, ollama_up=False)
    events: list = []
    asyncio.run(provision.provision("full", on_event=events.append))
    assert calls["pulls"] == 0  # daemon down -> can't pull
    assert any(e["dep"] == "ollama-daemon" and e["status"] == "needs_install" for e in events)


def test_provision_speaker_failure_is_fail_soft(monkeypatch, tmp_path):
    monkeypatch.setenv("BABY_HOME", str(tmp_path))
    _mock_primitives(monkeypatch)

    # Make only the optional CAM++ download fail; the run must still succeed.
    async def fake_download(url, dest, *, on_event, dep, retries=3):
        if dep == "speaker":
            raise RuntimeError("speaker mirror down")
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(b"x")
        on_event(provision._event(dep, "download", status="done", detail="fake"))

    monkeypatch.setattr(provision, "download_file", fake_download)
    events: list = []
    result = asyncio.run(provision.provision("cloud_only", on_event=events.append))
    assert result["ok"] is True  # optional failure never aborts
    assert any(e["dep"] == "speaker" and e["status"] == "skip" for e in events)


def test_provision_not_marked_when_verify_fails(monkeypatch, tmp_path):
    monkeypatch.setenv("BABY_HOME", str(tmp_path))
    _mock_primitives(monkeypatch)
    # Drive the functional re-verify to FAIL -> must not record provisioned.
    fail_result = [provision.health.Result("kokoro", True, False, "fail", "broke")]
    monkeypatch.setattr(provision.health, "run_all", lambda mode, level, browser: fail_result)
    result = asyncio.run(provision.provision("cloud_only", on_event=lambda e: None))
    assert result["ok"] is False
    assert paths.read_setup().get("provisioned") is not True


def test_provision_skips_already_present_assets(monkeypatch, tmp_path):
    monkeypatch.setenv("BABY_HOME", str(tmp_path))
    calls = _mock_primitives(monkeypatch)
    # Pre-create the kokoro assets under models_dir -> the idempotent re-run must skip.
    md = paths.models_dir()
    md.mkdir(parents=True, exist_ok=True)
    for name in ("kokoro-v1.0.onnx", "voices-v1.0.bin"):
        (md / name).write_bytes(b"already here")
    events: list = []
    asyncio.run(provision.provision("cloud_only", on_event=events.append))
    assert "kokoro" not in calls["downloads"]  # not re-downloaded
    assert any(e["dep"] == "kokoro" and e["status"] == "present" for e in events)


def test_provision_low_disk_short_circuits(monkeypatch, tmp_path):
    monkeypatch.setenv("BABY_HOME", str(tmp_path))
    calls = _mock_primitives(monkeypatch)
    monkeypatch.setattr(
        provision, "check_disk", lambda *a, **k: {"ok": False, "free_mb": 10, "need_mb": 9000}
    )
    result = asyncio.run(provision.provision("full", on_event=lambda e: None))
    assert result["ok"] is False and result["reason"] == "low_disk"
    assert not calls["downloads"] and calls["pulls"] == 0  # nothing fetched


# --- endpoints --------------------------------------------------------------


def _client(tmp_path, monkeypatch):
    monkeypatch.setenv("BABY_HOME", str(tmp_path))
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


def test_provision_endpoint_needs_a_mode(tmp_path, monkeypatch):
    client, db = _client(tmp_path, monkeypatch)
    try:
        r = client.post("/api/setup/provision")  # no mode chosen yet
        assert r.status_code == 400
    finally:
        asyncio.run(db.close())


def test_health_endpoint(tmp_path, monkeypatch):
    from core import health

    monkeypatch.setattr(
        health, "run_all",
        lambda mode, level, browser: [health.Result("torch", True, True, "pass", "ok")],
    )
    client, db = _client(tmp_path, monkeypatch)
    try:
        paths.write_setup({"install_mode": "cloud_only"})
        r = client.get("/api/setup/health").json()
        assert r["ok"] is True and "ready" in r["summary"].lower()
        assert r["results"][0]["name"] == "torch"
    finally:
        asyncio.run(db.close())


def test_plan_endpoint(tmp_path, monkeypatch):
    client, db = _client(tmp_path, monkeypatch)
    try:
        assert client.get("/api/setup/plan").status_code == 400  # no mode yet
        paths.write_setup({"install_mode": "cloud_only"})
        body = client.get("/api/setup/plan").json()
        assert body["mode"] == "cloud_only"
        keys = [s["key"] for s in body["steps"]]
        assert keys[0] == "disk" and keys[-1] == "verify" and "ollama-model" not in keys
    finally:
        asyncio.run(db.close())


def _drain_status(client) -> dict:
    """Poll /api/setup/status until the background task finishes (TestClient only
    advances the loop during a request)."""
    for _ in range(50):
        s = client.get("/api/setup/status").json()
        if not s["provisioning"]:
            return s
    return s


def test_provision_endpoint_captures_snapshot(tmp_path, monkeypatch):
    async def fake_provision(mode, *, on_event, browser=False):
        on_event(provision._event("kokoro", "download", status="done", detail="ok"))
        return {"ok": True}

    monkeypatch.setattr("core.provision.provision", fake_provision)
    client, db = _client(tmp_path, monkeypatch)
    try:
        paths.write_setup({"install_mode": "cloud_only"})
        assert client.post("/api/setup/provision").json()["status"] == "started"
        status = _drain_status(client)
        # /status is the reconnect snapshot -> it must hold the emitted event.
        assert status["progress"]["kokoro"]["status"] == "done"
    finally:
        asyncio.run(db.close())


def test_provision_endpoint_surfaces_a_failure(tmp_path, monkeypatch):
    async def boom(mode, *, on_event, browser=False):
        raise RuntimeError("provision blew up")

    monkeypatch.setattr("core.provision.provision", boom)
    client, db = _client(tmp_path, monkeypatch)
    try:
        paths.write_setup({"install_mode": "cloud_only"})
        client.post("/api/setup/provision")
        status = _drain_status(client)
        # The wrapper turns an exception into an error event, never a server crash.
        assert status["progress"]["provision"]["status"] == "error"
    finally:
        asyncio.run(db.close())
