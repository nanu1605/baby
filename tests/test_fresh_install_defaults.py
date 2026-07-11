"""The public installer's shipped default config must carry the most conservative
safety posture for a stranger's machine (DECISIONS #128): enforce mode, an empty
app-close allowlist, no owner PII, localhost-bound UI. This is the fresh-install
-defaults gate the plan promises -- it builds the REAL SafetyGate from the shipped
template and asserts its posture, not just the YAML values.
"""

from __future__ import annotations

from pathlib import Path

import yaml

from clients.cli import build_gate
from core.bus import EventBus

TEMPLATE = Path(__file__).resolve().parent.parent / "installer" / "config.default.yaml"


def _load() -> dict:
    with open(TEMPLATE, encoding="utf-8") as f:
        return yaml.safe_load(f)


def test_template_exists():
    assert TEMPLATE.exists(), "installer/config.default.yaml must ship with the installer"


def test_safety_mode_is_enforce():
    assert _load()["safety"]["mode"] == "enforce"


def test_app_close_allowlist_is_empty():
    # A fresh public install auto-allows NOTHING -- every app-close is confirmed.
    assert _load()["safety"]["auto_allow_app_close"] == []


def test_owner_pii_blank():
    owner = _load()["owner"]
    assert owner["name"] == ""
    assert owner["city"] == ""


def test_ui_bound_to_localhost():
    assert _load()["ui"]["host"] == "127.0.0.1"


def test_real_gate_from_template_is_conservative():
    # Construct the actual SafetyGate the app would build from this config.
    gate = build_gate(_load(), EventBus())
    assert gate.dry_run is False  # enforce, never dry_run in a shipped build
    assert gate.cfg.auto_allow_app_close == ()  # nothing auto-allowed
