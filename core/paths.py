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
