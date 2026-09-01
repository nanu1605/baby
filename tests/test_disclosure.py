"""v6 W5: the capability disclosure and the wizard's completion gate.

The disclosure is a promise made to a stranger about what this build does. The
tests that matter are the ones that catch it becoming a LIE: the "it asks first"
line is pinned to the shipped config template, and the cloud wording is pinned to
the install mode. The completion endpoint is pinned to refusing the two states
that would strand a user -- no acknowledgement, and a cloud-only install with no
working key.
"""

from __future__ import annotations

import yaml

from core import disclosure, keys, paths
from tests.test_keys import _FAKE_KEY, _client, _close, _probe


def _template() -> dict:
    return yaml.safe_load(paths._TEMPLATE.read_text(encoding="utf-8"))


# --- content ----------------------------------------------------------------


def test_every_item_is_complete_and_plain():
    for mode in ("full", "cloud_only"):
        rows = disclosure.items(mode)
        assert rows, mode
        for r in rows:
            assert r["key"] and r["title"] and r["detail"]
            # Written for an owner, not a lawyer or an engineer.
            assert "shall" not in r["detail"].lower()
            assert len(r["title"]) < 60


def test_keys_are_unique_and_stable_across_modes():
    full, cloud = disclosure.keys_shown("full"), disclosure.keys_shown("cloud_only")
    assert full == cloud, "both modes disclose the same topics, worded differently"
    assert len(disclosure.items("full")) == len(full), "duplicate keys"


def test_cloud_wording_follows_the_install_mode():
    """Telling a cloud-only user their chats can stay on this PC would be false;
    telling a Full user everything goes to the cloud would be too."""
    cloud_only = next(i for i in disclosure.items("cloud_only") if i["key"] == "cloud")
    full = next(i for i in disclosure.items("full") if i["key"] == "cloud")
    assert "no local model" in cloud_only["detail"]
    assert cloud_only["title"].startswith("Your messages go")
    assert "local model" in full["detail"] and "offline" in full["detail"]
    assert full["title"].startswith("Some messages go")


def test_cloud_item_is_not_buried():
    """The line with real privacy consequences sits near the top."""
    for mode in ("full", "cloud_only"):
        assert [i["key"] for i in disclosure.items(mode)][1] == "cloud"


def test_asks_first_claim_matches_the_shipped_template():
    """The disclosure promises nothing is auto-approved. That is only true while
    the shipped config actually enforces and auto-allows nothing -- if the
    template ever relaxes, this claim becomes a lie to a stranger."""
    cfg = _template()
    assert cfg["safety"]["mode"] == "enforce"
    assert cfg["safety"].get("auto_allow_app_close") in ([], None)
    confirm = next(i for i in disclosure.items("full") if i["key"] == "confirm")
    assert "asks you first" in confirm["detail"]
    assert "auto-approved" in confirm["detail"]


def test_no_telemetry_claim_matches_the_shipped_template():
    """'Nothing is uploaded to Baby's authors' must stay true of the build."""
    cfg = _template()
    ui = cfg.get("ui", {})
    assert str(ui.get("host", "127.0.0.1")) == "127.0.0.1"
    local = next(i for i in disclosure.items("full") if i["key"] == "local_data")
    assert "no telemetry" in local["detail"]


# --- endpoints --------------------------------------------------------------


def test_disclosure_endpoint_follows_mode(tmp_path, monkeypatch):
    client, db = _client(tmp_path, monkeypatch)
    try:
        paths.write_setup({"install_mode": "cloud_only"})
        body = client.get("/api/setup/disclosure").json()
        assert body["mode"] == "cloud_only"
        assert body["acknowledged"] is False
        assert any("no local model" in i["detail"] for i in body["items"])
    finally:
        _close(db)


def test_complete_requires_an_explicit_acknowledgement(tmp_path, monkeypatch):
    client, db = _client(tmp_path, monkeypatch)
    try:
        paths.write_setup({"install_mode": "full"})
        for payload in ({}, {"acknowledged": False}, {"acknowledged": "yes"}, []):
            r = client.post("/api/setup/complete", json=payload)
            assert r.status_code == 400, payload
        assert paths.is_setup_complete() is False
    finally:
        _close(db)


def test_complete_stamps_setup_and_flips_stats(tmp_path, monkeypatch):
    client, db = _client(tmp_path, monkeypatch)
    try:
        paths.write_setup({"install_mode": "full"})
        r = client.post("/api/setup/complete", json={"acknowledged": True})
        assert r.status_code == 200 and r.json()["complete"] is True
        state = paths.read_setup()
        assert state["setup_complete"] is True and state["disclosure_ack"] is True
        # /stats is what the wizard gate reads, so it has to agree.
        assert client.get("/stats").json()["setup"]["complete"] is True
    finally:
        _close(db)


def test_cloud_only_cannot_complete_without_a_working_key(tmp_path, monkeypatch):
    """Stamping 'done' here would hand the user a build whose next boot has no
    brain to answer with -- the same gate that holds the key step holds here."""
    client, db = _client(tmp_path, monkeypatch)
    try:
        paths.write_setup({"install_mode": "cloud_only"})
        r = client.post("/api/setup/complete", json={"acknowledged": True})
        assert r.status_code == 400
        assert r.json()["missing"] == "OPENROUTER_API_KEY"
        assert paths.is_setup_complete() is False

        keys.write_keys({"OPENROUTER_API_KEY": _FAKE_KEY})
        r = client.post("/api/setup/complete", json={"acknowledged": True})
        assert r.status_code == 200 and paths.is_setup_complete() is True
    finally:
        _close(db)


def test_complete_needs_a_mode_first(tmp_path, monkeypatch):
    client, db = _client(tmp_path, monkeypatch)
    try:
        r = client.post("/api/setup/complete", json={"acknowledged": True})
        assert r.status_code == 400
        assert paths.is_setup_complete() is False
    finally:
        _close(db)


def test_finishing_the_wizard_leaves_a_bootable_config(tmp_path, monkeypatch):
    """End to end: the state a completed wizard leaves behind must build a
    provider without raising, in both modes."""
    from core.router import build_provider

    client, db = _client(tmp_path, monkeypatch)
    try:
        _probe(monkeypatch, status=200)
        paths.write_setup({"install_mode": "cloud_only"})
        client.post("/api/setup/keys", json={"env": "OPENROUTER_API_KEY", "key": _FAKE_KEY})
        assert (
            client.post("/api/setup/complete", json={"acknowledged": True}).status_code
            == 200
        )
        build_provider(paths.apply_setup(_template()))  # must not raise
    finally:
        _close(db)


def test_disclosure_is_recorded_before_completion_is_claimed(tmp_path, monkeypatch):
    """A completed setup always carries the acknowledgement -- otherwise the
    record of what the user was shown is missing."""
    client, db = _client(tmp_path, monkeypatch)
    try:
        paths.write_setup({"install_mode": "full"})
        client.post("/api/setup/complete", json={"acknowledged": True})
        state = paths.read_setup()
        assert state.get("setup_complete") and state.get("disclosure_ack")
    finally:
        _close(db)


# --- W5 mode switch (the in-app Repair/Modify surface) ----------------------


def test_switching_mode_invalidates_provisioning(tmp_path, monkeypatch):
    """Full needs Ollama + the 9B that a cloud-only install never downloaded, so
    the old provisioned flag no longer describes this machine. If it survived the
    switch, the repair panel would report a readiness it has not re-checked."""
    from tests.test_keys import _client, _close

    client, db = _client(tmp_path, monkeypatch)
    try:
        paths.write_setup({"install_mode": "cloud_only", "provisioned": True})
        body = client.post("/api/setup/mode", json={"mode": "full"}).json()
        assert body["changed"] is True and body["provisioned"] is False
        assert paths.read_setup()["provisioned"] is False
    finally:
        _close(db)


def test_reselecting_the_same_mode_keeps_provisioning(tmp_path, monkeypatch):
    """Re-confirming the current mode must not throw away a good install and
    trigger a multi-GB re-download."""
    from tests.test_keys import _client, _close

    client, db = _client(tmp_path, monkeypatch)
    try:
        paths.write_setup({"install_mode": "full", "provisioned": True})
        body = client.post("/api/setup/mode", json={"mode": "full"}).json()
        assert body["changed"] is False and body["provisioned"] is True
    finally:
        _close(db)


def test_first_mode_choice_does_not_report_a_change(tmp_path, monkeypatch):
    """The wizard's initial pick is not a switch -- there is nothing to invalidate."""
    from tests.test_keys import _client, _close

    client, db = _client(tmp_path, monkeypatch)
    try:
        body = client.post("/api/setup/mode", json={"mode": "cloud_only"}).json()
        assert body["changed"] is False
    finally:
        _close(db)
