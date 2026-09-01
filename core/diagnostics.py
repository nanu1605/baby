"""v6 W5: a diagnostics report a stranger can safely paste into a public issue.

When someone's install misbehaves, the useful reply is "send me your
diagnostics" -- but the obvious version of that feature hands a public bug
tracker the user's API keys, their Windows username, and whatever they happened
to say to their assistant. So the scrubber is the feature here, not the
collector.

What the report contains: versions, the install mode and provisioning flags, the
functional health summary, GPU/disk facts, which keys are configured (present or
not -- never the value), and the tail of the log. What it must never contain is
enforced by `scrub()` and pinned by a regression test.

Scrubbing is layered, because each layer catches what the others cannot:

  1. EXACT known secrets. Every value currently in .env is replaced wherever it
     appears. This is the only layer that catches a key with an unusual shape.
  2. Key-SHAPED strings. Catches a key that is no longer in .env -- an old one
     still sitting in a log line from weeks ago, or one from a provider we do
     not know about.
  3. Personal identifiers. The Windows username (which appears in nearly every
     path), the owner's name and city from config.yaml, and email addresses.

Redaction is one-way and lossy on purpose: the report is meant to be readable,
not reversible. Nothing here writes anything back into the user's state.
"""

from __future__ import annotations

import os
import platform
import re
import shutil
import sys
from datetime import UTC, datetime
from pathlib import Path

from core import paths

# How much of the log to include. Enough to hold a crash and what led to it,
# small enough to paste. Read from the END of the file.
LOG_TAIL_BYTES = 64_000

_REDACTED = "[redacted]"

# Layer 2: key-shaped strings, independent of whether we know the value.
# Deliberately loose on the tail (vendors change alphabets) and anchored on the
# vendor prefix, which is the part that does not change.
_KEY_SHAPES = (
    re.compile(r"\bsk-[A-Za-z0-9_\-]{16,}", re.I),  # OpenAI-style, incl. sk-or-
    re.compile(r"\bnvapi-[A-Za-z0-9_\-]{16,}", re.I),  # NVIDIA
    re.compile(r"\bAIza[A-Za-z0-9_\-]{20,}"),  # Google
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}"),  # GitHub, in case one is pasted
    re.compile(r"\bBearer\s+[A-Za-z0-9._\-]{16,}", re.I),  # any auth header echo
)

_EMAIL = re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.]+\b")


def _known_secrets() -> list[str]:
    """Every value currently in .env, longest first.

    Longest first matters: if two secrets share a prefix, replacing the short one
    first would leave the tail of the long one exposed.
    """
    from core import keys as keymod

    values = [v.strip() for v in keymod.read_env_file().values() if v and v.strip()]
    # A short value is not a secret worth matching -- redacting "1" or "true"
    # everywhere would destroy the report.
    return sorted({v for v in values if len(v) >= 8}, key=len, reverse=True)


def _personal_terms() -> list[str]:
    """Identifiers that are not secrets but should not go in a public issue."""
    terms: list[str] = []
    user = (os.environ.get("USERNAME") or "").strip()
    if len(user) >= 3:
        terms.append(user)
    try:
        import yaml

        cfg_path = paths.config_path()
        if cfg_path.exists():
            cfg = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
            owner = cfg.get("owner") or {}
            for field in ("name", "city"):
                val = str(owner.get(field) or "").strip()
                if len(val) >= 3:
                    terms.append(val)
    except Exception:  # noqa: BLE001 -- diagnostics must never fail on bad config
        pass
    return sorted(set(terms), key=len, reverse=True)


def scrub(text: str, *, extra: list[str] | None = None) -> str:
    """Remove secrets and personal identifiers from a block of text.

    Order is deliberate: exact known values first (so a key is redacted even if
    its shape is unfamiliar), then shapes (so an old key still in a log is caught
    even though it is no longer in .env), then personal terms.
    """
    if not text:
        return ""
    out = text
    for secret in list(extra or []) + _known_secrets():
        if secret:
            out = out.replace(secret, _REDACTED)
    for pattern in _KEY_SHAPES:
        out = pattern.sub(_REDACTED, out)
    out = _EMAIL.sub(_REDACTED, out)
    for term in _personal_terms():
        # Whole-word, case-insensitive: a username also appears inside paths.
        out = re.sub(rf"\b{re.escape(term)}\b", "[user]", out, flags=re.I)
    return out


def log_tail(limit: int = LOG_TAIL_BYTES) -> str:
    """The last `limit` bytes of the log, scrubbed. "" when there is no log."""
    log = paths.baby_home() / "logs" / "baby.log"
    if not log.exists():
        return ""
    try:
        size = log.stat().st_size
        with log.open("rb") as fh:
            if size > limit:
                fh.seek(size - limit)
            raw = fh.read()
    except OSError as exc:
        return f"(could not read the log: {type(exc).__name__})"
    text = raw.decode("utf-8", errors="replace")
    if size > limit:
        text = text.split("\n", 1)[-1]  # drop the partial first line
    return scrub(text)


def _disk() -> dict:
    try:
        usage = shutil.disk_usage(paths.baby_home())
        return {
            "free_gb": round(usage.free / 1024**3, 1),
            "total_gb": round(usage.total / 1024**3, 1),
        }
    except OSError:
        return {}


def _versions() -> dict:
    app = "unknown"
    try:
        import tomllib

        pyproject = Path(__file__).resolve().parent.parent / "pyproject.toml"
        if pyproject.exists():
            app = tomllib.loads(pyproject.read_text(encoding="utf-8"))["project"]["version"]
    except Exception:  # noqa: BLE001 -- a missing version must not break the report
        pass
    return {
        "baby": app,
        "python": sys.version.split()[0],
        "os": f"{platform.system()} {platform.release()}",
    }


def collect(health: dict | None = None) -> dict:
    """Assemble the report. `health` is an already-run health payload, if any.

    Everything textual goes through scrub() on the way out, including the health
    summary -- a failing probe can quote a path or a URL.
    """
    from core import keys as keymod

    setup = paths.read_setup()
    mode = setup.get("install_mode") or "unknown"
    report = {
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "versions": _versions(),
        "install": {
            "installed": paths.is_installed(),
            "mode": mode,
            "provisioned": bool(setup.get("provisioned")),
            "setup_complete": bool(setup.get("setup_complete")),
            "router_mode": setup.get("router_mode"),
            "disclosure_ack": bool(setup.get("disclosure_ack")),
        },
        "disk": _disk(),
        # Presence only. The masked form is deliberately NOT included: a report
        # is pasted in public, and the last 4 characters are still key material.
        "keys": [
            {"env": row["env"], "present": row["present"], "required": row["required"]}
            for row in keymod.key_status(mode)
        ],
        "health": None,
        "log_tail": log_tail(),
    }
    if health is not None:
        report["health"] = {
            "ok": bool(health.get("ok")),
            "summary": scrub(str(health.get("summary") or "")),
            "failures": [
                {
                    "name": r.get("name"),
                    "detail": scrub(str(r.get("detail") or ""))[:400],
                }
                for r in (health.get("results") or [])
                if not r.get("ok")
            ],
        }
    return report


def render(report: dict) -> str:
    """The report as plain text, ready to paste into an issue."""
    lines: list[str] = ["Baby diagnostics", "=" * 40, ""]
    v = report.get("versions", {})
    lines.append(f"generated   {report.get('generated_at')}")
    lines.append(f"baby        {v.get('baby')}")
    lines.append(f"python      {v.get('python')}")
    lines.append(f"os          {v.get('os')}")
    lines.append("")

    inst = report.get("install", {})
    lines.append("install")
    for k in ("installed", "mode", "provisioned", "setup_complete", "router_mode"):
        lines.append(f"  {k:<16}{inst.get(k)}")
    disk = report.get("disk", {})
    if disk:
        lines.append(f"  {'disk free':<16}{disk.get('free_gb')} GB of {disk.get('total_gb')} GB")
    lines.append("")

    lines.append("api keys (configured or not -- values are never included)")
    for row in report.get("keys", []):
        state = "set" if row["present"] else ("MISSING" if row["required"] else "not set")
        lines.append(f"  {row['env']:<22}{state}")
    lines.append("")

    health = report.get("health")
    if health is not None:
        lines.append(f"health      {'OK' if health['ok'] else 'PROBLEMS'}")
        if health.get("summary"):
            lines.append(f"  {health['summary']}")
        for f in health.get("failures", []):
            lines.append(f"  FAIL {f['name']}: {f['detail']}")
        lines.append("")

    lines.append("recent log (scrubbed)")
    lines.append("-" * 40)
    lines.append(report.get("log_tail") or "(no log file yet)")
    return "\n".join(lines)


def write_report(text: str) -> Path:
    """Save the rendered report next to the logs and return its path."""
    out_dir = paths.baby_home() / "logs"
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    path = out_dir / f"baby-diagnostics-{stamp}.txt"
    path.write_text(text, encoding="utf-8")
    return path
