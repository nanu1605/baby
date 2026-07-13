"""v6: where Baby's writable state lives.

A dev checkout keeps everything cwd-relative exactly as before -- so the dev
workflow is byte-identical. An installed build (Program Files is read-only for a
non-admin user) sets `BABY_HOME` to a per-user writable dir (`%LOCALAPPDATA%\baby`,
already home to logs/browser/shots), and config.yaml / .env / baby.db resolve
there instead.

The rule is deliberately opt-in: **nothing changes unless `BABY_HOME` is set.**
The installer/shell sets it; a `python run.py` from the repo never does, so no
existing behavior moves. Data caches (logs, browser profile, screenshots) keep
their own `%LOCALAPPDATA%\baby` resolution untouched.
"""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

# The conservative default config the installer ships (installer/config.default.yaml,
# see W1b). Seeded into BABY_HOME on first run; never overwrites an existing config.
_TEMPLATE = Path(__file__).resolve().parent.parent / "installer" / "config.default.yaml"


def baby_home() -> Path:
    """State root. `BABY_HOME` when set (installed), else the current dir (dev)."""
    home = os.environ.get("BABY_HOME")
    return Path(home) if home else Path.cwd()


def config_path() -> Path:
    return baby_home() / "config.yaml"


def env_path() -> Path:
    return baby_home() / ".env"


def db_path() -> Path:
    return baby_home() / "baby.db"


def ensure_config(template: Path | None = None) -> Path:
    """Seed `BABY_HOME/config.yaml` from the conservative template on first run,
    only if it does not already exist (never clobber a returning user's config).

    In dev, `BABY_HOME` is unset so this targets `cwd/config.yaml`, which already
    exists -- a no-op. Returns the resolved config path either way.
    """
    target = config_path()
    src = template or _TEMPLATE
    if not target.exists() and src.exists():
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, target)
    return target


# --- first-run wizard state (v6 W2) -----------------------------------------
# The wizard's choices live in a SEPARATE BABY_HOME/setup.json, never by
# rewriting config.yaml (yaml round-tripping would strip the template's comments).
# The overlay is applied non-destructively at load; no setup.json => no change,
# so a dev checkout stays byte-identical.


def setup_path() -> Path:
    return baby_home() / "setup.json"


def read_setup() -> dict:
    """The wizard state dict, or {} when absent/unreadable (never raises)."""
    p = setup_path()
    if p.exists():
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except (json.JSONDecodeError, OSError):
            return {}
    return {}


def write_setup(updates: dict) -> dict:
    """Merge `updates` into BABY_HOME/setup.json (created if absent). Returns the
    merged state. Only non-secret wizard flags belong here -- keys go to .env."""
    state = read_setup()
    state.update(updates)
    p = setup_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(state, indent=2), encoding="utf-8")
    return state


def apply_setup(config: dict) -> dict:
    """Overlay the wizard's choices onto a freshly loaded config, in place.

    Currently only `router_mode` (the wizard UPGRADES local_primary ->
    cloud_primary after a cloud key validates in W4). A missing setup.json is a
    no-op, so pre-wizard / dev boots are unchanged.
    """
    setup = read_setup()
    mode = setup.get("router_mode")
    if mode:
        config.setdefault("router", {})["mode"] = mode
    return config


def is_setup_complete() -> bool:
    return bool(read_setup().get("setup_complete"))
