"""v6 W4: the cloud API keys a stranger must supply, validated before they count.

Three keys sit behind Baby's cloud ladder, and the installer has to collect them
from someone who has never seen this codebase:

  OPENROUTER_API_KEY  the cloud PRIMARY brain (models.nim_primary). Required for
                      a cloud-only install -- `router.mode: cloud_primary` raises
                      at boot when this slot has no key (core/router.py), and a
                      cloud-only box has no Ollama to fall back to.
  GEMINI_API_KEY      the optional backstop (models.cloud).
  NVIDIA_API_KEY      the optional heavy planning brain (models.nim_heavy).

SECURITY POSTURE -- the reason this module exists rather than a few inline lines:

  * A key is NEVER logged, echoed back in a response, put in a URL, or written to
    setup.json. Only `.env` holds key material. Everything this module returns to
    a caller is either a boolean, a classification, or a `mask()`ed form.
  * Validation is a REAL network call before the key is trusted, so a typo is
    caught in the wizard instead of at the first cloud escalation. It is a
    GET /models auth check (the same probe NvidiaProvider.probe already uses):
    it proves DNS + TLS + auth without spending generation quota.
  * The key travels in an Authorization header, never a query string.
  * `.env` is written with inheritance stripped and a single owner-only grant, so
    a loosened parent ACL cannot widen it after the fact.

Additive: nothing here edits the router, the providers, or the safety gate. The
wizard stamps `router_mode` through the existing paths.write_setup overlay.
"""

from __future__ import annotations

import os
import re
import subprocess
import threading
from dataclasses import dataclass
from pathlib import Path

import httpx

from core import paths

# Auth probes are a single round trip against a CDN-fronted endpoint; a stalled
# connection should surface as "couldn't reach it", not hang the wizard.
VALIDATE_TIMEOUT_S = 12.0


@dataclass(frozen=True)
class KeySpec:
    """One API key the wizard can collect."""

    env: str
    label: str
    role: str  # primary | backstop | heavy
    base_url: str  # OpenAI-compatible root; {base_url}/models is the auth probe
    signup_url: str  # "get a key" deep link shown in the wizard
    prefix: str  # expected leading marker, "" when the vendor has none
    required_for: tuple[str, ...]  # install modes that cannot finish without it
    note: str


# Ordered: the wizard asks for the primary first, then the optional extras.
# base_url mirrors installer/config.default.yaml so the probe hits the same host
# the provider will.
KEYS: tuple[KeySpec, ...] = (
    KeySpec(
        env="OPENROUTER_API_KEY",
        label="OpenRouter",
        role="primary",
        base_url="https://openrouter.ai/api/v1",
        signup_url="https://openrouter.ai/keys",
        prefix="sk-or-",
        required_for=("cloud_only",),
        note="The main cloud brain. Required for a cloud-only install; optional "
        "on a Full install, which can run entirely on your own GPU.",
    ),
    KeySpec(
        env="GEMINI_API_KEY",
        label="Google Gemini",
        role="backstop",
        base_url="https://generativelanguage.googleapis.com/v1beta/openai",
        signup_url="https://aistudio.google.com/apikey",
        prefix="",
        required_for=(),
        note="Optional free-tier backstop used when the main brain is rate "
        "limited or down.",
    ),
    KeySpec(
        env="NVIDIA_API_KEY",
        label="NVIDIA NIM",
        role="heavy",
        base_url="https://integrate.api.nvidia.com/v1",
        signup_url="https://build.nvidia.com/",
        prefix="nvapi-",
        required_for=(),
        note="Optional heavier model for planning-grade requests.",
    ),
)

_BY_ENV = {k.env: k for k in KEYS}


def spec(env: str) -> KeySpec | None:
    return _BY_ENV.get(env)


def required_key(mode: str) -> KeySpec | None:
    """The key an install mode cannot finish without, if any.

    Only cloud-only has one: with no local model on the box, an unkeyed cloud
    ladder leaves Baby with nothing to answer with (and cloud_primary refuses to
    build). A Full install may finish keyless and stay local_primary.
    """
    for k in KEYS:
        if mode in k.required_for:
            return k
    return None


# --- masking ----------------------------------------------------------------


def mask(key: str | None) -> str:
    """A safe-to-display, safe-to-log rendering of a key.

    Shows the vendor prefix (useful: it tells the user *which* key they pasted)
    and the last 4 characters, never more. A key too short to mask usefully
    collapses to a fixed placeholder rather than leaking a prefix of itself.
    """
    k = (key or "").strip()
    if not k:
        return "(not set)"
    if len(k) < 12:
        return "****"
    head = ""
    for s in KEYS:
        if s.prefix and k.startswith(s.prefix):
            head = s.prefix
            break
    return f"{head}...{k[-4:]}"


# --- validation -------------------------------------------------------------


def looks_like(env: str, key: str) -> bool:
    """Cheap client-side shape check, used only to warn -- never to reject.

    Vendors change prefixes; the network probe is the real authority. This exists
    so an obvious paste error (a whole URL, a truncated key) gets a hint before a
    round trip.
    """
    s = spec(env)
    k = (key or "").strip()
    if not k or len(k) < 12 or any(c.isspace() for c in k):
        return False
    if s and s.prefix:
        return k.startswith(s.prefix)
    return True


def _classify_status(code: int, label: str) -> dict:
    """Map an auth-probe HTTP status onto an actionable outcome.

    401/403 is the case that matters: the key is wrong, and saying so plainly is
    the entire value of validating. 429 means the key is GOOD but throttled -- it
    must not be rejected, or a rate-limited user can never finish the wizard.
    """
    if code == 200:
        return {"ok": True, "kind": "valid", "message": f"{label} key works."}
    if code in (401, 403):
        return {
            "ok": False,
            "kind": "invalid_key",
            "message": f"{label} rejected this key. Check for a truncated paste or "
            "a key that was revoked, then try again.",
        }
    if code == 402:
        return {
            "ok": False,
            "kind": "no_credit",
            "message": f"The key is valid but the {label} account has no credit "
            "left. Top it up, or use a different key.",
        }
    if code == 429:
        return {
            "ok": True,
            "kind": "rate_limited",
            "message": f"{label} accepted the key but is rate limiting right now. "
            "Saved anyway -- it should settle on its own.",
        }
    if 500 <= code < 600:
        return {
            "ok": False,
            "kind": "server_error",
            "message": f"{label} is having trouble right now (HTTP {code}). This is "
            "on their side -- retry in a minute.",
        }
    return {
        "ok": False,
        "kind": "unexpected",
        "message": f"{label} answered with an unexpected HTTP {code}. Retry, and if "
        "it persists the key may be for a different product.",
    }


async def validate_key(env: str, key: str, *, timeout: float = VALIDATE_TIMEOUT_S) -> dict:
    """Prove a key works before it is written anywhere.

    Returns {ok, kind, message} and NEVER the key. `kind` is one of:
    valid | rate_limited (both ok) | empty | invalid_key | no_credit |
    server_error | unexpected | network | unknown_key.

    The probe is GET {base_url}/models with a Bearer header -- auth-only, so it
    costs no tokens and works identically for all three OpenAI-compatible hosts.
    """
    s = spec(env)
    if s is None:
        return {"ok": False, "kind": "unknown_key", "message": f"Unknown key {env}."}
    k = (key or "").strip()
    if not k:
        return {"ok": False, "kind": "empty", "message": "Paste a key first."}
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.get(
                f"{s.base_url.rstrip('/')}/models",
                headers={"Authorization": f"Bearer {k}"},
            )
    except httpx.HTTPError as exc:
        # Reuse the provisioning classifier so "no internet" reads the same here
        # as it does during the download step. The key lives in a header, not the
        # URL, so httpx has no reason to put it in the message -- but this is a
        # third party's error string on a security-critical path, so scrub the key
        # out of it before anything looks at it rather than trusting that.
        from core.provision import classify_error

        detail = classify_error(f"{type(exc).__name__}: {exc}".replace(k, "<key>"))
        return {
            "ok": False,
            "kind": "network",
            "message": f"Couldn't reach {s.label}. {detail['message']}",
        }
    return _classify_status(resp.status_code, s.label)


# --- .env persistence -------------------------------------------------------

_LINE = re.compile(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=")

# .env is read-merge-written, so concurrent saves must not interleave and drop a
# key. The wizard can easily produce two in flight (Save on two rows in a row).
_WRITE_LOCK = threading.Lock()


def read_env_file(path: Path | None = None) -> dict[str, str]:
    """Parse BABY_HOME/.env into a dict. Values stay in memory only.

    Deliberately tolerant and dependency-free: it must not fail on a file a user
    hand-edited. Unparseable lines are ignored here but PRESERVED by write_keys.
    """
    p = path or paths.env_path()
    out: dict[str, str] = {}
    if not p.exists():
        return out
    try:
        text = p.read_text(encoding="utf-8")
    except OSError:
        return out
    for raw in text.splitlines():
        m = _LINE.match(raw)
        if not m:
            continue
        value = raw.split("=", 1)[1].strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        out[m.group(1)] = value
    return out


def secure_file(path: Path) -> bool:
    """Restrict a file to its owner. Returns whether the tightening succeeded.

    On Windows: strip inherited ACEs and grant the current user alone. The parent
    (%LOCALAPPDATA%) is already per-user by default, so this is defense in depth
    against a loosened parent rather than the only barrier. On POSIX: chmod 600.

    Fail-soft on purpose -- a machine where icacls is missing or policy-blocked
    still gets a working install; the caller reports `secured: false` instead of
    losing the key. Never raises, never logs the file's contents.
    """
    try:
        if os.name != "nt":
            path.chmod(0o600)
            return True
        user = os.environ.get("USERNAME") or ""
        if not user:
            return False
        domain = os.environ.get("USERDOMAIN") or ""
        account = f"{domain}\\{user}" if domain else user
        # Passed as a list, so subprocess quotes an account name containing a
        # space ("CORP SERVERS\user") for the Windows command line. A failure
        # here is still non-fatal -- the caller reports secured: false.
        proc = subprocess.run(
            ["icacls", str(path), "/inheritance:r", "/grant:r", f"{account}:(R,W)"],
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )
        return proc.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def write_keys(values: dict[str, str], path: Path | None = None) -> dict:
    """Merge validated keys into BABY_HOME/.env and lock the file down.

    Rewrites only the lines it owns: an existing `KEY=` line is replaced in place,
    a new one is appended, and every other line -- comments, unrelated vars, a
    user's hand edits -- is preserved verbatim. An empty value REMOVES that key's
    line (how the wizard clears a key it could not validate).

    Also updates os.environ so the same process can use the key immediately
    without a reload. Returns {path, written, secured} -- names only, no values.

    Serialised: two quick saves from the wizard would otherwise interleave
    read-merge-write and silently drop one of the keys.
    """
    p = path or paths.env_path()
    with _WRITE_LOCK:
        secured = _write_env(p, values)

    for name, new in ((k, (v or "").strip()) for k, v in values.items()):
        if new:
            os.environ[name] = new
        else:
            os.environ.pop(name, None)

    return {"path": str(p), "written": sorted(values), "secured": secured}


def _write_env(p: Path, values: dict[str, str]) -> bool:
    """The merge-and-swap half of write_keys. Returns whether the ACL was tightened."""
    p.parent.mkdir(parents=True, exist_ok=True)
    # surrogateescape round-trips bytes we did not write (a user's hand edit saved
    # in the system codepage) losslessly, instead of raising and losing their file.
    existing = (
        p.read_text(encoding="utf-8", errors="surrogateescape").splitlines()
        if p.exists()
        else []
    )

    pending = {k: (v or "").strip() for k, v in values.items()}
    out: list[str] = []
    for raw in existing:
        m = _LINE.match(raw)
        name = m.group(1) if m else None
        if name in pending:
            new = pending.pop(name)
            if new:  # replace in place, keeping ordering stable
                out.append(f"{name}={new}")
            # else: drop the line entirely (key cleared)
            continue
        out.append(raw)
    for name, new in pending.items():
        if new:
            out.append(f"{name}={new}")

    body = "\n".join(out).rstrip("\n")
    # Write, then tighten, then swap: the temp file lives in the same per-user dir
    # for the moment it holds key material, so no wider audience ever sees it. Any
    # failure before the swap deletes the temp rather than leaving key material
    # behind in a file nothing will ever clean up.
    tmp = p.with_suffix(p.suffix + ".tmp")
    try:
        tmp.write_text(
            body + "\n" if body else "", encoding="utf-8", errors="surrogateescape"
        )
        secured = secure_file(tmp)
        os.replace(tmp, p)
    except BaseException:
        # BaseException, not Exception: a KeyboardInterrupt here would otherwise
        # strand a readable temp file holding the key.
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        raise
    if not secured:
        secured = secure_file(p)
    return secured


# --- wizard-facing status ---------------------------------------------------


def key_status(mode: str | None = None) -> list[dict]:
    """Per-key state for the wizard. Masked only -- no raw key ever leaves here.

    `present` reads .env and the live environment together, so a key exported by
    the shell counts as configured even before anything is written to disk.
    """
    env_file = read_env_file()
    rows: list[dict] = []
    for s in KEYS:
        value = (env_file.get(s.env) or os.environ.get(s.env) or "").strip()
        rows.append(
            {
                "env": s.env,
                "label": s.label,
                "role": s.role,
                "signup_url": s.signup_url,
                "prefix": s.prefix,
                "note": s.note,
                "required": bool(mode and mode in s.required_for),
                "present": bool(value),
                "masked": mask(value),
            }
        )
    return rows


def has_key(env: str) -> bool:
    value = (read_env_file().get(env) or os.environ.get(env) or "").strip()
    return bool(value)


def router_mode_for(mode: str) -> str:
    """The router mode the wizard should stamp for an install mode + key state.

    cloud_primary needs the OpenRouter slot keyed or `build_provider` raises at
    boot, so it is only ever chosen once that key is actually present. A Full
    install with no cloud key stays local_primary and runs off the local 9B.
    """
    return "cloud_primary" if has_key("OPENROUTER_API_KEY") else "local_primary"


def can_finish(mode: str) -> dict:
    """Whether the wizard may complete for this install mode.

    Cloud-only without the primary key would boot into a crash, so it is blocked
    here rather than discovered on the next launch.
    """
    need = required_key(mode)
    if need is None or has_key(need.env):
        return {"ok": True, "missing": None, "message": ""}
    return {
        "ok": False,
        "missing": need.env,
        "message": f"A cloud-only install needs a working {need.label} key -- "
        "there is no local model on this machine to answer without one.",
    }
