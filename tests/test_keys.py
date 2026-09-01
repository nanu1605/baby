"""v6 W4: API-key registry, validation, and secure .env persistence.

The security-critical claims are what these assert: a key never appears in a
returned payload or a masked form, the auth probe sends it as a header rather
than a query string, an unreachable host reads as "network" and not "bad key",
a rate-limited key is still accepted, and writing .env preserves every line the
wizard does not own.
"""

from __future__ import annotations

import asyncio

import httpx
import pytest

from core import keys

# A synthetic, obviously-fake key. Never a real credential -- this file is
# scanned by the secret gate, and the name says what it is.
_FAKE_KEY = "sk-or-v1-notarealkey-tail"


# --- registry ---------------------------------------------------------------


def test_registry_shape():
    envs = [k.env for k in keys.KEYS]
    assert envs == ["OPENROUTER_API_KEY", "GEMINI_API_KEY", "NVIDIA_API_KEY"]
    for k in keys.KEYS:
        assert k.base_url.startswith("https://")
        assert k.signup_url.startswith("https://")
        assert k.label and k.note
        assert k.role in ("primary", "backstop", "heavy")


def test_only_cloud_only_has_a_required_key():
    # A Full install can finish keyless (it has the local 9B); cloud-only cannot,
    # because router.mode cloud_primary raises at boot without the primary slot.
    need = keys.required_key("cloud_only")
    assert need is not None and need.env == "OPENROUTER_API_KEY"
    assert keys.required_key("full") is None


def test_base_urls_match_the_shipped_config():
    """The probe must hit the same host the provider will, or a key can validate
    here and still fail at runtime."""
    import yaml

    from core import paths

    cfg = yaml.safe_load(paths._TEMPLATE.read_text(encoding="utf-8"))
    models = cfg["models"]
    assert models["nim_primary"]["base_url"].rstrip("/") == keys.spec(
        "OPENROUTER_API_KEY"
    ).base_url.rstrip("/")
    assert models["nim_primary"]["api_key_env"] == "OPENROUTER_API_KEY"
    assert models["nim_heavy"]["base_url"].rstrip("/") == keys.spec(
        "NVIDIA_API_KEY"
    ).base_url.rstrip("/")


# --- masking ----------------------------------------------------------------


def test_mask_never_leaks_the_key():
    m = keys.mask(_FAKE_KEY)
    assert _FAKE_KEY not in m
    assert m.endswith("tail")
    assert m.startswith("sk-or-")
    # Only the last 4 characters survive.
    assert "notarealkey" not in m


def test_mask_empty_and_short():
    assert keys.mask("") == "(not set)"
    assert keys.mask(None) == "(not set)"
    # Too short to mask usefully -> a fixed placeholder, not a prefix of itself.
    assert keys.mask("sk-or-ab") == "****"


def test_looks_like_is_advisory_only():
    assert keys.looks_like("OPENROUTER_API_KEY", _FAKE_KEY) is True
    assert keys.looks_like("OPENROUTER_API_KEY", "nvapi-1234567890abc") is False
    assert keys.looks_like("OPENROUTER_API_KEY", "https://openrouter.ai/keys") is False
    assert keys.looks_like("OPENROUTER_API_KEY", "short") is False
    # Gemini has no stable prefix, so any plausible-length key passes the hint.
    assert keys.looks_like("GEMINI_API_KEY", "AIzaSyExample1234567") is True


# --- validation -------------------------------------------------------------


def _probe(monkeypatch, *, status: int | None = None, exc: Exception | None = None):
    """Stand in for the network, capturing how the key was actually sent."""
    seen: dict = {}

    class _Resp:
        status_code = status

    class _Client:
        def __init__(self, *a, **kw):
            seen["timeout"] = kw.get("timeout")

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, url, headers=None):
            seen["url"] = url
            seen["headers"] = headers or {}
            if exc is not None:
                raise exc
            return _Resp()

    monkeypatch.setattr(httpx, "AsyncClient", _Client)
    return seen


def test_valid_key_sends_a_bearer_header_and_no_query_string(monkeypatch):
    seen = _probe(monkeypatch, status=200)
    out = asyncio.run(keys.validate_key("OPENROUTER_API_KEY", _FAKE_KEY))
    assert out["ok"] is True and out["kind"] == "valid"
    # The key travels in a header...
    assert seen["headers"]["Authorization"] == f"Bearer {_FAKE_KEY}"
    # ...never in the URL, and the probe is the auth-only /models endpoint.
    assert _FAKE_KEY not in seen["url"]
    assert "?" not in seen["url"] and seen["url"].endswith("/models")
    # And the outcome carries no key material.
    assert _FAKE_KEY not in str(out)


def test_bad_key_is_named_as_a_bad_key(monkeypatch):
    for code in (401, 403):
        _probe(monkeypatch, status=code)
        out = asyncio.run(keys.validate_key("OPENROUTER_API_KEY", _FAKE_KEY))
        assert out["ok"] is False and out["kind"] == "invalid_key"
        assert _FAKE_KEY not in out["message"]


def test_rate_limited_key_is_still_accepted(monkeypatch):
    # 429 means the key WORKS. Rejecting it would trap a throttled user in the
    # wizard with no way to finish.
    _probe(monkeypatch, status=429)
    out = asyncio.run(keys.validate_key("OPENROUTER_API_KEY", _FAKE_KEY))
    assert out["ok"] is True and out["kind"] == "rate_limited"


def test_no_credit_and_server_error_are_distinguished(monkeypatch):
    _probe(monkeypatch, status=402)
    assert asyncio.run(keys.validate_key("OPENROUTER_API_KEY", _FAKE_KEY))["kind"] == "no_credit"
    _probe(monkeypatch, status=503)
    out = asyncio.run(keys.validate_key("OPENROUTER_API_KEY", _FAKE_KEY))
    assert out["kind"] == "server_error" and out["ok"] is False


def test_offline_reads_as_network_not_bad_key(monkeypatch):
    """The failure that would otherwise send a user hunting for a new key."""
    _probe(monkeypatch, exc=httpx.ConnectError("client error (Connect)"))
    out = asyncio.run(keys.validate_key("OPENROUTER_API_KEY", _FAKE_KEY))
    assert out["ok"] is False and out["kind"] == "network"
    assert "reach" in out["message"].lower()
    assert _FAKE_KEY not in out["message"]


def test_empty_and_unknown_key_short_circuit(monkeypatch):
    seen = _probe(monkeypatch, status=200)
    assert asyncio.run(keys.validate_key("OPENROUTER_API_KEY", "  "))["kind"] == "empty"
    assert asyncio.run(keys.validate_key("NOPE_API_KEY", _FAKE_KEY))["kind"] == "unknown_key"
    assert "url" not in seen  # neither case touched the network


# --- .env persistence -------------------------------------------------------


@pytest.fixture()
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("BABY_HOME", str(tmp_path))
    for k in keys.KEYS:
        monkeypatch.delenv(k.env, raising=False)
    return tmp_path


def test_write_creates_env_and_sets_process_environ(home):
    out = keys.write_keys({"OPENROUTER_API_KEY": _FAKE_KEY})
    env = home / ".env"
    assert env.exists()
    assert env.read_text(encoding="utf-8").strip() == f"OPENROUTER_API_KEY={_FAKE_KEY}"
    # Usable immediately, without waiting for a reload.
    import os

    assert os.environ["OPENROUTER_API_KEY"] == _FAKE_KEY
    # The receipt names keys, never values.
    assert out["written"] == ["OPENROUTER_API_KEY"]
    assert _FAKE_KEY not in str(out)


def test_write_preserves_unrelated_lines_and_comments(home):
    env = home / ".env"
    env.write_text(
        "# my notes\nSOMETHING_ELSE=keepme\nOPENROUTER_API_KEY=old-value\n# trailing\n",
        encoding="utf-8",
    )
    keys.write_keys({"OPENROUTER_API_KEY": _FAKE_KEY})
    text = env.read_text(encoding="utf-8")
    assert "# my notes" in text and "# trailing" in text
    assert "SOMETHING_ELSE=keepme" in text
    assert "old-value" not in text
    assert f"OPENROUTER_API_KEY={_FAKE_KEY}" in text
    # Replaced in place, not appended -- exactly one line for the key.
    assert text.count("OPENROUTER_API_KEY=") == 1


def test_empty_value_clears_the_key(home):
    keys.write_keys({"GEMINI_API_KEY": "AIzaSyExample1234567"})
    assert keys.has_key("GEMINI_API_KEY")
    keys.write_keys({"GEMINI_API_KEY": ""})
    import os

    assert "GEMINI_API_KEY" not in (home / ".env").read_text(encoding="utf-8")
    assert os.environ.get("GEMINI_API_KEY") is None
    assert keys.has_key("GEMINI_API_KEY") is False


def test_read_env_file_tolerates_junk(home):
    (home / ".env").write_text(
        'export QUOTED="q-value"\nnot a var line\n\nPLAIN=p-value\n', encoding="utf-8"
    )
    parsed = keys.read_env_file()
    assert parsed["QUOTED"] == "q-value"  # export + quotes stripped
    assert parsed["PLAIN"] == "p-value"
    assert "not a var line" not in parsed


def test_write_leaves_no_temp_file_behind(home):
    keys.write_keys({"OPENROUTER_API_KEY": _FAKE_KEY})
    leftovers = [p.name for p in home.iterdir() if p.name.startswith(".env.")]
    assert leftovers == []


def test_secure_file_failure_does_not_lose_the_key(home, monkeypatch):
    """A machine where icacls is blocked still gets a working install; the caller
    just learns the file could not be tightened."""
    monkeypatch.setattr(keys, "secure_file", lambda p: False)
    out = keys.write_keys({"OPENROUTER_API_KEY": _FAKE_KEY})
    assert out["secured"] is False
    assert f"OPENROUTER_API_KEY={_FAKE_KEY}" in (home / ".env").read_text(encoding="utf-8")


def test_concurrent_saves_do_not_drop_a_key(home):
    """Two Save clicks in quick succession read-merge-write the same file; without
    serialisation one of the keys is silently lost."""
    import threading

    payloads = [
        {"OPENROUTER_API_KEY": _FAKE_KEY},
        {"GEMINI_API_KEY": "AIzaSyExample1234567"},
        {"NVIDIA_API_KEY": "nvapi-example1234567"},
    ]
    barrier = threading.Barrier(len(payloads))

    def go(vals):
        barrier.wait()
        keys.write_keys(vals)

    threads = [threading.Thread(target=go, args=(v,)) for v in payloads]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    text = (home / ".env").read_text(encoding="utf-8")
    for v in payloads:
        for name in v:
            assert name in text, f"{name} was lost to a concurrent write"


def test_hand_edited_non_utf8_line_survives_a_save(home):
    """A user editing .env in Notepad can leave system-codepage bytes behind.
    Reading strict UTF-8 would raise and cost them the file."""
    latin1 = b"NOTE=caf\xe9 latte\nOPENROUTER_API_KEY=old\n"
    (home / ".env").write_bytes(latin1)
    keys.write_keys({"OPENROUTER_API_KEY": _FAKE_KEY})
    raw = (home / ".env").read_bytes()
    assert b"caf\xe9 latte" in raw  # round-tripped byte for byte
    assert _FAKE_KEY.encode() in raw
    assert b"old" not in raw


def test_a_failed_swap_leaves_no_key_bearing_temp_file(home, monkeypatch):
    """If anything fails between writing the temp file and swapping it in, the
    temp must not survive holding the key."""
    import os as _os

    def boom(src, dst):
        raise OSError("swap failed")

    monkeypatch.setattr(_os, "replace", boom)
    with pytest.raises(OSError):
        keys.write_keys({"OPENROUTER_API_KEY": _FAKE_KEY})
    leftovers = [p.name for p in home.iterdir() if p.name.startswith(".env")]
    assert leftovers == [], f"key material stranded in {leftovers}"


def test_transport_error_text_is_scrubbed_of_the_key(monkeypatch):
    """Third-party error strings are not ours to trust on a secrets path."""
    _probe(monkeypatch, exc=httpx.ConnectError(f"connect failed for {_FAKE_KEY}"))
    out = asyncio.run(keys.validate_key("OPENROUTER_API_KEY", _FAKE_KEY))
    assert _FAKE_KEY not in str(out)
    assert out["kind"] == "network"


# --- wizard-facing status ---------------------------------------------------


def test_key_status_is_masked_and_marks_the_required_one(home):
    keys.write_keys({"OPENROUTER_API_KEY": _FAKE_KEY})
    rows = keys.key_status("cloud_only")
    assert _FAKE_KEY not in str(rows)
    primary = next(r for r in rows if r["env"] == "OPENROUTER_API_KEY")
    assert primary["present"] is True and primary["required"] is True
    assert primary["masked"].endswith("tail")
    gemini = next(r for r in rows if r["env"] == "GEMINI_API_KEY")
    assert gemini["present"] is False and gemini["required"] is False
    assert gemini["masked"] == "(not set)"
    # Full mode marks nothing as required.
    assert all(r["required"] is False for r in keys.key_status("full"))


def test_can_finish_blocks_keyless_cloud_only(home):
    blocked = keys.can_finish("cloud_only")
    assert blocked["ok"] is False and blocked["missing"] == "OPENROUTER_API_KEY"
    assert blocked["message"]
    # Full may finish keyless -- it has a local brain.
    assert keys.can_finish("full")["ok"] is True
    keys.write_keys({"OPENROUTER_API_KEY": _FAKE_KEY})
    assert keys.can_finish("cloud_only")["ok"] is True


def test_router_mode_follows_the_primary_key(home):
    # Keyless -> local_primary, which boots without raising.
    assert keys.router_mode_for("full") == "local_primary"
    keys.write_keys({"OPENROUTER_API_KEY": _FAKE_KEY})
    assert keys.router_mode_for("full") == "cloud_primary"
    assert keys.router_mode_for("cloud_only") == "cloud_primary"


def test_backstop_key_alone_does_not_earn_cloud_primary(home):
    """Gemini fills models.cloud, not models.nim_primary -- and cloud_primary
    raises at boot when the primary slot is unkeyed."""
    keys.write_keys({"GEMINI_API_KEY": "AIzaSyExample1234567"})
    assert keys.router_mode_for("cloud_only") == "local_primary"
    assert keys.can_finish("cloud_only")["ok"] is False


# --- the boot contract (the crash this phase exists to prevent) -------------


def test_stamped_cloud_primary_actually_boots(home, monkeypatch):
    """The positive half of the keyless-boot guard.

    test_fresh_install_defaults proves the shipped template boots keyless on
    local_primary. This proves the OTHER direction: once a validated key is
    written and the wizard stamps cloud_primary through the setup overlay,
    build_provider must succeed. If these two ever disagree, a stranger's first
    launch after finishing the wizard dies before uvicorn binds -- silently,
    under pythonw.
    """
    import yaml

    from core import paths
    from core.router import build_provider

    keys.write_keys({"OPENROUTER_API_KEY": _FAKE_KEY})
    mode = keys.router_mode_for("cloud_only")
    assert mode == "cloud_primary"
    paths.write_setup({"install_mode": "cloud_only", "router_mode": mode})

    config = paths.apply_setup(yaml.safe_load(paths._TEMPLATE.read_text(encoding="utf-8")))
    assert config["router"]["mode"] == "cloud_primary"
    build_provider(config)  # must not raise


def test_wizard_never_stamps_a_mode_that_would_crash(home):
    """Whatever key state the user lands in, the stamped mode has to boot."""
    import yaml

    from core import paths
    from core.router import build_provider

    for present in ({}, {"GEMINI_API_KEY": "AIzaSyExample1234567"},
                    {"OPENROUTER_API_KEY": _FAKE_KEY}):
        keys.write_keys({k.env: present.get(k.env, "") for k in keys.KEYS})
        for install_mode in ("full", "cloud_only"):
            paths.write_setup({"router_mode": keys.router_mode_for(install_mode)})
            cfg = paths.apply_setup(
                yaml.safe_load(paths._TEMPLATE.read_text(encoding="utf-8"))
            )
            build_provider(cfg)  # must not raise, in any key state


# --- endpoints --------------------------------------------------------------


def _client(tmp_path, monkeypatch):
    """A real app instance, so the key handlers are exercised end to end."""
    import asyncio as _asyncio

    from fastapi.testclient import TestClient

    from core.agent import AgentCore
    from core.bus import EventBus
    from core.safety import SafetyConfig, SafetyGate
    from db.database import Database
    from tests.conftest import FakeProvider
    from ui.server import UIContext, create_app

    monkeypatch.setenv("BABY_HOME", str(tmp_path))
    for k in keys.KEYS:
        monkeypatch.delenv(k.env, raising=False)

    db = Database(tmp_path / "s.db")

    async def _boot():
        await db.connect()
        return await db.create_conversation("ui")

    conv = _asyncio.run(_boot())
    bus = EventBus()
    gate = SafetyGate(SafetyConfig(mode="dry_run"), bus)
    agent = AgentCore(FakeProvider([]), db, conv, channel="ui", bus=bus, gate=gate)
    ctx = UIContext(
        db=db,
        bus=bus,
        gate=gate,
        agent=agent,
        config={"models": {"daily": {"provider": "ollama", "model": "m"}}},
    )
    return TestClient(create_app(ctx)), db


def _close(db):
    import asyncio as _asyncio

    _asyncio.run(db.close())


def test_keys_endpoint_lists_masked_state(tmp_path, monkeypatch):
    client, db = _client(tmp_path, monkeypatch)
    try:
        from core import paths

        paths.write_setup({"install_mode": "cloud_only"})
        keys.write_keys({"OPENROUTER_API_KEY": _FAKE_KEY})
        r = client.get("/api/setup/keys")
        assert r.status_code == 200
        body = r.json()
        assert body["mode"] == "cloud_only"
        assert len(body["keys"]) == 3
        assert body["can_finish"]["ok"] is True
        # The response must never carry key material.
        assert _FAKE_KEY not in r.text
        assert "...tail" in r.text  # ...only the mask
    finally:
        _close(db)


def test_validate_endpoint_does_not_persist(tmp_path, monkeypatch):
    client, db = _client(tmp_path, monkeypatch)
    try:
        _probe(monkeypatch, status=200)
        r = client.post(
            "/api/setup/keys/validate",
            json={"env": "OPENROUTER_API_KEY", "key": _FAKE_KEY},
        )
        assert r.status_code == 200 and r.json()["kind"] == "valid"
        assert _FAKE_KEY not in r.text
        # Nothing written: validation is a dry run.
        assert not (tmp_path / ".env").exists()
        assert keys.has_key("OPENROUTER_API_KEY") is False
    finally:
        _close(db)


def test_save_rejects_a_bad_key_without_writing(tmp_path, monkeypatch):
    client, db = _client(tmp_path, monkeypatch)
    try:
        _probe(monkeypatch, status=401)
        r = client.post(
            "/api/setup/keys", json={"env": "OPENROUTER_API_KEY", "key": _FAKE_KEY}
        )
        assert r.status_code == 400
        assert r.json()["saved"] is False and r.json()["kind"] == "invalid_key"
        assert _FAKE_KEY not in r.text
        assert not (tmp_path / ".env").exists()
    finally:
        _close(db)


def test_save_persists_and_upgrades_router_mode(tmp_path, monkeypatch):
    client, db = _client(tmp_path, monkeypatch)
    try:
        from core import paths

        paths.write_setup({"install_mode": "cloud_only"})
        _probe(monkeypatch, status=200)
        r = client.post(
            "/api/setup/keys", json={"env": "OPENROUTER_API_KEY", "key": _FAKE_KEY}
        )
        assert r.status_code == 200
        body = r.json()
        assert body["saved"] is True
        assert body["router_mode"] == "cloud_primary"
        assert body["restart_required"] is True
        assert body["can_finish"]["ok"] is True
        assert _FAKE_KEY not in r.text
        # Persisted to .env and stamped into setup.json (never into setup.json's values).
        assert f"OPENROUTER_API_KEY={_FAKE_KEY}" in (tmp_path / ".env").read_text(
            encoding="utf-8"
        )
        setup = paths.read_setup()
        assert setup["router_mode"] == "cloud_primary"
        assert _FAKE_KEY not in (tmp_path / "setup.json").read_text(encoding="utf-8")
    finally:
        _close(db)


def test_save_unknown_key_is_rejected(tmp_path, monkeypatch):
    client, db = _client(tmp_path, monkeypatch)
    try:
        r = client.post("/api/setup/keys", json={"env": "SECRET_KEY", "key": _FAKE_KEY})
        assert r.status_code == 400
        r = client.post(
            "/api/setup/keys/validate", json={"env": "SECRET_KEY", "key": _FAKE_KEY}
        )
        assert r.status_code == 400
    finally:
        _close(db)


def test_clearing_a_key_downgrades_router_mode(tmp_path, monkeypatch):
    client, db = _client(tmp_path, monkeypatch)
    try:
        from core import paths

        paths.write_setup({"install_mode": "full"})
        keys.write_keys({"OPENROUTER_API_KEY": _FAKE_KEY})
        r = client.post("/api/setup/keys", json={"env": "OPENROUTER_API_KEY", "key": ""})
        assert r.status_code == 200
        # Removing the primary key must drop back to a mode that still boots.
        assert r.json()["router_mode"] == "local_primary"
        assert paths.read_setup()["router_mode"] == "local_primary"
    finally:
        _close(db)


def test_keyless_cloud_only_cannot_finish(tmp_path, monkeypatch):
    client, db = _client(tmp_path, monkeypatch)
    try:
        from core import paths

        paths.write_setup({"install_mode": "cloud_only"})
        body = client.get("/api/setup/keys").json()
        assert body["can_finish"]["ok"] is False
        assert body["can_finish"]["missing"] == "OPENROUTER_API_KEY"
    finally:
        _close(db)


def test_malformed_body_is_rejected_without_echoing_it(tmp_path, monkeypatch):
    """FastAPI's own validation error echoes the request body back in
    detail[].input -- so declaring `body: dict` would reflect a key straight into
    a 422 for any client that posted the wrong shape. These handlers parse the
    JSON themselves and answer with a fixed message instead."""
    client, db = _client(tmp_path, monkeypatch)
    try:
        for payload in ([_FAKE_KEY], _FAKE_KEY, {"env": ["x"], "key": _FAKE_KEY},
                        {"env": "OPENROUTER_API_KEY", "key": [_FAKE_KEY]}):
            for url in ("/api/setup/keys", "/api/setup/keys/validate"):
                r = client.post(url, json=payload)
                assert r.status_code == 400, (url, payload)
                assert _FAKE_KEY not in r.text
        # Non-JSON bodies too.
        r = client.post("/api/setup/keys", content=_FAKE_KEY.encode())
        assert r.status_code == 400 and _FAKE_KEY not in r.text
    finally:
        _close(db)


# --- the log-scan gate ------------------------------------------------------


def test_no_key_material_reaches_logs_bus_or_disk(tmp_path, monkeypatch, caplog):
    """The W4 secrets gate, as a regression test rather than a one-off check.

    Drives the real endpoints with a sentinel key through every branch a user can
    hit -- a good key, a rejected key, and a network failure -- while capturing
    Python logging, every bus event, and every byte written under BABY_HOME. The
    sentinel may appear in exactly one place: .env. Anywhere else (a log line, a
    bus payload, an HTTP response, setup.json) is a leak.
    """
    import logging

    sentinel = "sk-or-v1-canary-do-not-log-0001"
    client, db = _client(tmp_path, monkeypatch)
    try:
        from core import paths

        paths.write_setup({"install_mode": "cloud_only"})

        # Record every bus event the key handlers publish.
        published: list[str] = []
        from core.bus import EventBus

        real_publish = EventBus.publish

        def spy(self, kind, channel, **payload):
            published.append(f"{kind}|{channel}|{payload}")
            return real_publish(self, kind, channel, **payload)

        monkeypatch.setattr(EventBus, "publish", spy)

        responses: list[str] = []
        with caplog.at_level(logging.DEBUG):
            _probe(monkeypatch, status=200)
            responses.append(
                client.post(
                    "/api/setup/keys", json={"env": "OPENROUTER_API_KEY", "key": sentinel}
                ).text
            )
            responses.append(client.get("/api/setup/keys").text)
            _probe(monkeypatch, status=401)
            responses.append(
                client.post(
                    "/api/setup/keys", json={"env": "NVIDIA_API_KEY", "key": sentinel}
                ).text
            )
            responses.append(
                client.post(
                    "/api/setup/keys/validate",
                    json={"env": "GEMINI_API_KEY", "key": sentinel},
                ).text
            )
            _probe(monkeypatch, exc=httpx.ConnectError("client error (Connect)"))
            responses.append(
                client.post(
                    "/api/setup/keys/validate",
                    json={"env": "GEMINI_API_KEY", "key": sentinel},
                ).text
            )

        # 1. No HTTP response may carry the key.
        for body in responses:
            assert sentinel not in body

        # 2. No log record -- message or formatted output.
        for rec in caplog.records:
            assert sentinel not in rec.getMessage()
        assert sentinel not in caplog.text

        # 3. No bus event.
        assert published, "the spy never fired -- the gate would pass vacuously"
        for ev in published:
            assert sentinel not in ev

        # 4. On disk: .env and nothing else.
        holders = []
        for path in tmp_path.rglob("*"):
            if not path.is_file():
                continue
            try:
                if sentinel in path.read_text(encoding="utf-8", errors="ignore"):
                    holders.append(path.name)
            except OSError:
                continue
        assert holders == [".env"], f"key material found in {holders}"
    finally:
        _close(db)
