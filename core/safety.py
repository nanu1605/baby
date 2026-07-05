"""Safety gate: deterministic shell/tool classifier + user confirmation flow.

The most important file in the repo. Rules are regex-based and unit-tested;
the LLM NEVER classifies its own commands. DENY patterns win over everything;
unknown commands default to CONFIRM (never allow-by-default).
"""

from __future__ import annotations

import asyncio
import enum
import re
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from core.bus import EventBus


class SafetyClass(enum.StrEnum):
    ALLOW = "allow"
    CONFIRM = "confirm"
    DENY = "deny"


_SEVERITY = {SafetyClass.ALLOW: 0, SafetyClass.CONFIRM: 1, SafetyClass.DENY: 2}


@dataclass
class Verdict:
    klass: SafetyClass
    reason: str
    segment: str = ""


@dataclass
class SafetyConfig:
    mode: str = "enforce"  # enforce | dry_run
    auto_allow_app_close: tuple[str, ...] = ()
    confirm_timeout_s: float = 60.0
    home: Path = field(default_factory=Path.home)


@dataclass
class SafetySession:
    """Per-process approval state (Phase 4/5).

    browser_domain_fn reads the domain from the REAL Playwright page — the
    model's kwargs are never trusted for it, so the LLM can't talk its way
    past a per-domain confirm by claiming a different site.

    unverified_channels (Phase 5): channels whose CURRENT turn failed speaker
    verification — every tool call on them is DENIED at the gate (chat-only).
    Channel-scoped so a concurrent UI or task turn keeps its tools.
    """

    confirmed_browser_domains: set = field(default_factory=set)
    browser_domain_fn: object | None = None  # Callable[[], str]
    unverified_channels: set = field(default_factory=set)

    def current_browser_domain(self) -> str:
        if self.browser_domain_fn is None:
            return ""
        try:
            return self.browser_domain_fn() or ""
        except Exception:  # noqa: BLE001 — a broken page reads as "no page"
            return ""


# Processes whose termination can take down the session or the OS.
SYSTEM_PROCESSES = frozenset(
    {"csrss", "wininit", "winlogon", "lsass", "services", "svchost", "smss", "dwm"}
)

# --- whole-string DENY pre-checks (defeat obfuscation before any splitting) --

_PS_TOKEN = re.compile(r"(?i)\b(powershell|pwsh)(\.exe)?\b")
# Any unambiguous prefix of -EncodedCommand: -e, -ec, -en, -enc, -encoded...
_ENCODED_FLAG = re.compile(r"(?i)(^|\s)-e(c|n[a-z]*)?(\s|$)")
_PRECHECK_DENY: list[tuple[re.Pattern, str]] = [
    (
        re.compile(r"(?i)(^|[\s;|&(={\"'])(invoke-expression|iex)\b"),
        "Invoke-Expression executes arbitrary strings — always denied",
    ),
    (
        re.compile(r"(?i)frombase64string"),
        "base64-decoded execution is always denied",
    ),
    (
        re.compile(r"(?i)download(string|file)\s*\("),
        "download-and-execute pattern is always denied",
    ),
]

_CMD_C = re.compile(r"(?i)\bcmd(\.exe)?\s+/c\s+(.+)", re.DOTALL)
_SUBEXPR = re.compile(r"\$\(([^)]*)\)")
_SCRIPTBLOCK = re.compile(r"\{([^{}]*)\}")
_SPLIT = re.compile(r"&&|\|\||[;|&\n]")

# --- per-segment rule tables --------------------------------------------------

_PROTECTED_DIR = re.compile(
    r"(?i)(c:[\\/]+windows|\bsystem32\b|program files|\$env:windir"
    r"|%windir%|%systemroot%|\$env:systemroot)"
)
# A bare drive root ("C:\", "D:") or the all-users dir as a standalone token.
_ROOT_TOKEN = re.compile(r"(?i)(^|\s|\"|')([a-z]:[\\/]?|c:[\\/]+users[\\/]?)(\"|'|\s*$)")
_DELETE_VERB = re.compile(r"(?i)(^|\s|\"|')(remove-item|del|erase|rd|rmdir|rm)(\.exe)?\b")
_WRITE_VERB = re.compile(
    r"(?i)(^|\s)(set-content|add-content|out-file|new-item|move-item|copy-item"
    r"|rename-item|attrib|icacls|takeown)\b"
)
_KILL_VERB = re.compile(r"(?i)(^|\s|\"|')(stop-process|taskkill|pskill|kill)(\.exe)?\b")
_SYSTEM_PROC = re.compile(r"(?i)\b(" + "|".join(sorted(SYSTEM_PROCESSES)) + r")(\.exe)?\b")
_SHUTDOWN = re.compile(r"(?i)(^|\s|\"|')(stop-computer|restart-computer|shutdown)(\.exe)?\b")
_SHUTDOWN_INTENT = re.compile(
    r"(?i)(shut\s*down|restart|reboot|power\s*off|switch\s*off|band\s+kar|bandh\s+kar)"
)
_REDIRECT = re.compile(r"(?<!\d)>{1,2}")
_REDIRECT_PROTECTED = re.compile(
    r"(?i)>{1,2}\s*\"?\s*(c:[\\/]+windows|\S*system32|c:[\\/]+program files)"
)
# Destructive intent in a natural-language background-task spec (Phase 4):
# routing every spec through classify_shell would gate ALL of them (unknown →
# CONFIRM), so a keyword scan implements "confirm if spec contains gated
# actions" without gating benign research tasks.
_TASK_GATED_RE = re.compile(
    r"(?i)\b(delete|remove|erase|wipe|uninstall|install|kill|terminate|close|shut\s*down|"
    r"restart|reboot|format|registry|regedit|pay|purchase|buy|order|send|upload|email|"
    r"message|post|publish|tweet|delete\s+file|overwrite|modify|hata\s*d\w*|band\s+kar|"
    r"bandh\s+kar|udaa?\s*d\w*)\b"
)

_SEGMENT_DENY: list[tuple[re.Pattern, str]] = [
    (re.compile(r"(?i)(^|\s|\"|')format(\.com|\.exe)?\s+[a-z]:"), "disk format"),
    (re.compile(r"(?i)(^|\s|\"|')(diskpart|bcdedit|mkfs)(\.exe)?\b"), "disk/boot tampering"),
    (re.compile(r"(?i)(^|\s|\"|')vssadmin(\.exe)?\b.*delete"), "shadow-copy deletion"),
    (re.compile(r"(?i)(^|\s|\"|')cipher(\.exe)?\s+/w"), "secure disk wipe"),
    (re.compile(r"(?i)\brm\s+(-[a-z]*\s+)*-?[a-z]*rf?[a-z]*\s+/(\s|$)"), "recursive root delete"),
    (
        re.compile(
            r"(?i)\breg(\.exe)?\s+delete\s+\"?(hklm|hkcr|hku|hkey_local_machine|hkey_classes_root)"
        ),
        "machine-wide registry deletion",
    ),
    (
        re.compile(r"(?i)\b(remove-item|remove-itemproperty)\b.*hklm:"),
        "machine-wide registry deletion",
    ),
]

_SET_EXECUTION_POLICY = re.compile(r"(?i)(^|\s)set-executionpolicy\b")
_EP_SAFE_SCOPE = re.compile(r"(?i)-scope\s+(process|currentuser)")

_ALLOW_FIRST_TOKENS = frozenset(
    {
        "dir",
        "ls",
        "gci",
        "type",
        "cat",
        "gc",
        "echo",
        "write-output",
        "write-host",
        "whoami",
        "hostname",
        "systeminfo",
        "tasklist",
        "ipconfig",
        "ping",
        "tracert",
        "nslookup",
        "netstat",
        "pwd",
        "cd",
        "sl",
        "set-location",
        "get-location",
        "test-path",
        "select-string",
        "findstr",
        "where",
        "measure-object",
        "sort-object",
        "select-object",
        "where-object",
        "foreach-object",
        "format-table",
        "format-list",
        "out-string",
        "date",
        "help",
        "get-help",
        "tree",
        "ver",
        "time",
        "more",
        "clip",
        "hostname.exe",
    }
)
_GIT_READONLY = re.compile(
    r"(?i)^git\s+(status|log|diff|show|branch|remote|stash\s+list|rev-parse|describe|blame)\b"
)
_VERSION_CHECK = re.compile(
    r"(?i)^(python|python3|py|pip|pip3|uv|node|npm|git|ruff|ollama)\s+(--version|-v|version)\s*$"
)
_PIP_READONLY = re.compile(r"(?i)^(pip|pip3|uv\s+pip)\s+(list|show|freeze)\b")

_SEGMENT_CONFIRM: list[tuple[re.Pattern, str]] = [
    (re.compile(r"(?i)^(new-item|mkdir|md)\b"), "creates files or directories"),
    (re.compile(r"(?i)^(copy-item|copy|cp|xcopy|robocopy)\b"), "copies files"),
    (re.compile(r"(?i)^(move-item|move|mv)\b"), "moves files"),
    (re.compile(r"(?i)^(remove-item|del|erase|rd|rmdir|rm)\b"), "deletes files"),
    (re.compile(r"(?i)^(rename-item|ren)\b"), "renames files"),
    (re.compile(r"(?i)^(set-content|add-content|out-file|tee-object)\b"), "writes file content"),
    (re.compile(r"(?i)^set-\w+"), "changes system or session state"),
    (
        re.compile(
            r"(?i)^(pip|pip3|uv|winget|npm|yarn|pnpm|cargo|choco)\s+(install|add|remove|uninstall|upgrade|update)\b"
        ),
        "installs or removes software",
    ),
    (
        re.compile(
            r"(?i)^git\s+(push|commit|add|checkout|switch|reset|merge|rebase|clean|rm|mv|restore)\b"
        ),
        "modifies the git repository",
    ),
    (re.compile(r"(?i)^(taskkill|stop-process|kill|pskill)\b"), "terminates a process"),
    (re.compile(r"(?i)^(compress-archive|expand-archive|tar)\b"), "creates or extracts archives"),
    (
        re.compile(r"(?i)^(curl|wget|invoke-webrequest|iwr|invoke-restmethod|irm)\b"),
        "fetches from the network",
    ),
    (re.compile(r"(?i)^(sc|net|netsh|schtasks)\b"), "changes system services or configuration"),
]


def _first_token(segment: str) -> str:
    token = segment.strip().split()[0] if segment.strip() else ""
    token = token.strip("\"'")
    token = token.rsplit("\\", 1)[-1].rsplit("/", 1)[-1]
    return token.lower().removesuffix(".exe")


def _classify_segment(segment: str, user_text: str | None) -> Verdict:
    seg = segment.strip()
    if not seg:
        return Verdict(SafetyClass.ALLOW, "empty segment", seg)

    # DENY rules first — always win.
    if _DELETE_VERB.search(seg) and (_PROTECTED_DIR.search(seg) or _ROOT_TOKEN.search(seg)):
        return Verdict(SafetyClass.DENY, "deletion targeting a protected system path", seg)
    if _WRITE_VERB.search(seg) and _PROTECTED_DIR.search(seg):
        return Verdict(SafetyClass.DENY, "modification of a protected system path", seg)
    if _KILL_VERB.search(seg) and _SYSTEM_PROC.search(seg):
        return Verdict(SafetyClass.DENY, "would kill a critical system process", seg)
    for pattern, reason in _SEGMENT_DENY:
        if pattern.search(seg):
            return Verdict(SafetyClass.DENY, reason, seg)
    if _SET_EXECUTION_POLICY.search(seg) and not _EP_SAFE_SCOPE.search(seg):
        return Verdict(SafetyClass.DENY, "machine-wide execution-policy change", seg)
    if _REDIRECT_PROTECTED.search(seg):
        return Verdict(SafetyClass.DENY, "output redirected into a protected system path", seg)
    if _SHUTDOWN.search(seg):
        if user_text and _SHUTDOWN_INTENT.search(user_text):
            return Verdict(SafetyClass.CONFIRM, "shuts down or restarts the PC (you asked)", seg)
        return Verdict(SafetyClass.DENY, "shutdown/restart not requested by you", seg)

    # Redirection floors at CONFIRM before any ALLOW match.
    if _REDIRECT.search(seg):
        return Verdict(SafetyClass.CONFIRM, "writes output to a file", seg)

    token = _first_token(seg)
    if (
        token in _ALLOW_FIRST_TOKENS
        or token.startswith("get-")
        or _GIT_READONLY.match(seg)
        or _VERSION_CHECK.match(seg)
        or _PIP_READONLY.match(seg)
    ):
        return Verdict(SafetyClass.ALLOW, "read-only command", seg)

    stripped = seg if seg.lower().startswith(token) else seg
    for pattern, reason in _SEGMENT_CONFIRM:
        if pattern.match(stripped) or pattern.match(token):
            return Verdict(SafetyClass.CONFIRM, reason, seg)

    return Verdict(SafetyClass.CONFIRM, "unrecognized command — asking to be safe", seg)


def classify_shell(command: str, user_text: str | None = None) -> Verdict:
    """Classify a shell command. Deterministic; DENY-first; unknown → CONFIRM."""
    cmd = (command or "").strip()
    if not cmd:
        return Verdict(SafetyClass.CONFIRM, "empty command", cmd)

    # Whole-string pre-checks defeat encoding/eval obfuscation.
    if _PS_TOKEN.search(cmd) and _ENCODED_FLAG.search(cmd):
        return Verdict(SafetyClass.DENY, "encoded PowerShell command — always denied", cmd)
    for pattern, reason in _PRECHECK_DENY:
        if pattern.search(cmd):
            return Verdict(SafetyClass.DENY, reason, cmd)

    segments = [cmd]
    if match := _CMD_C.search(cmd):
        segments.append(match.group(2).strip().strip("\"'"))
    # $( ... ) subexpressions and { ... } scriptblocks hide nested commands.
    segments.extend(_SUBEXPR.findall(cmd))
    segments.extend(_SCRIPTBLOCK.findall(cmd))

    # Quote-unaware split: a ';' inside quotes yields a garbage extra segment
    # that classifies as unknown → CONFIRM. Fail-safe, never fail-open.
    pieces: list[str] = []
    for segment in segments:
        pieces.extend(p for p in _SPLIT.split(segment) if p.strip())

    verdict = Verdict(SafetyClass.ALLOW, "read-only command", cmd)
    for piece in pieces:
        piece_verdict = _classify_segment(piece, user_text)
        if _SEVERITY[piece_verdict.klass] > _SEVERITY[verdict.klass]:
            verdict = piece_verdict
        if verdict.klass is SafetyClass.DENY:
            return verdict

    # Backticks are PowerShell's escape character; splitting around them
    # correctly is not worth it — floor at CONFIRM instead.
    if "`" in cmd and verdict.klass is SafetyClass.ALLOW:
        verdict = Verdict(
            SafetyClass.CONFIRM, "contains escape characters — asking to be safe", cmd
        )
    return verdict


def classify_tool(
    tool: str,
    kwargs: dict,
    cfg: SafetyConfig,
    user_text: str | None = None,
    session: SafetySession | None = None,
    channel: str = "",
) -> Verdict:
    """Single gate entry for every registered tool."""
    # Speaker verification (Phase 5): an unverified voice turn gets NO tools
    # at all — checked first so the LLM cannot reach any other branch.
    if session is not None and channel and channel in session.unverified_channels:
        return Verdict(
            SafetyClass.DENY,
            "voice not recognized as the owner — chat only",
            tool,
        )

    if tool == "browser_act":
        action = str(kwargs.get("action", "")).lower().strip()
        if action in ("goto", "read", "screenshot"):
            return Verdict(SafetyClass.ALLOW, f"browser {action} (read-only)", action)
        if action in ("click", "type", "press"):
            domain = session.current_browser_domain() if session else ""
            if not domain:
                return Verdict(
                    SafetyClass.CONFIRM, f"browser {action} with no page open yet", action
                )
            if domain in session.confirmed_browser_domains:
                return Verdict(
                    SafetyClass.ALLOW, f"already approved for {domain} this session", domain
                )
            return Verdict(
                SafetyClass.CONFIRM, f"first {action} on {domain} this session", domain
            )
        return Verdict(SafetyClass.CONFIRM, f"unknown browser action {action!r}", action)

    if tool == "run_shell":
        return classify_shell(str(kwargs.get("command", "")), user_text)

    if tool == "write_file":
        raw = str(kwargs.get("path", ""))
        try:
            path = Path(raw).expanduser().resolve()
        except (OSError, ValueError):
            return Verdict(SafetyClass.DENY, f"unresolvable path: {raw!r}", raw)
        if not path.is_relative_to(cfg.home):
            return Verdict(
                SafetyClass.DENY, "writes are restricted to your user profile", str(path)
            )
        return Verdict(SafetyClass.CONFIRM, f"writes to {path}", str(path))

    if tool == "app_control":
        action = str(kwargs.get("action", "")).lower()
        name = str(kwargs.get("name", "")).lower().removesuffix(".exe")
        if action != "close":
            return Verdict(SafetyClass.ALLOW, f"app {action or 'query'}", name)
        if name in SYSTEM_PROCESSES:
            return Verdict(SafetyClass.DENY, "critical system process", name)
        if name in {n.lower() for n in cfg.auto_allow_app_close}:
            return Verdict(SafetyClass.ALLOW, "app close (auto-allowed)", name)
        return Verdict(SafetyClass.CONFIRM, f"closes {name or 'an app'}", name)

    if tool == "describe_screen":
        return Verdict(
            SafetyClass.ALLOW,
            "screen capture (read-only; stays local unless local vision fails)",
            tool,
        )

    if tool in ("start_background_task", "start_project"):
        text = f"{kwargs.get('title', '')} {kwargs.get('spec', '')}"
        if _TASK_GATED_RE.search(text):
            return Verdict(
                SafetyClass.CONFIRM,
                "the task description mentions a gated action — confirm before queuing "
                "(each tool inside the task is still gated individually)",
                str(kwargs.get("title", "")),
            )
        return Verdict(SafetyClass.ALLOW, "background task (research/benign)", tool)

    return Verdict(SafetyClass.ALLOW, "read-only tool", tool)


# --- confirmation flow --------------------------------------------------------


class ConfirmationManager:
    """Pending user confirmations keyed by id, resolved from any surface."""

    def __init__(self, bus: EventBus, timeout_s: float = 60.0) -> None:
        self.bus = bus
        self.timeout_s = timeout_s
        self._pending: dict[str, asyncio.Future[bool]] = {}

    async def ask(
        self, *, tool: str, command: str, explanation: str, channel: str
    ) -> tuple[bool, str]:
        """Publish a confirm_request and await the decision. Timeout → NO."""
        confirm_id = uuid.uuid4().hex[:8]
        future: asyncio.Future[bool] = asyncio.get_running_loop().create_future()
        self._pending[confirm_id] = future
        self.bus.publish(
            "confirm_request",
            channel,
            confirm_id=confirm_id,
            tool=tool,
            command=command,
            explanation=explanation,
            timeout_s=self.timeout_s,
        )
        try:
            approved = await asyncio.wait_for(future, self.timeout_s)
            resolution = "approved" if approved else "refused"
        except TimeoutError:
            approved, resolution = False, "timeout"
        except asyncio.CancelledError:
            self._pending.pop(confirm_id, None)
            self.bus.publish(
                "confirm_resolved",
                channel,
                confirm_id=confirm_id,
                approved=False,
                resolution="cancelled",
            )
            raise
        finally:
            self._pending.pop(confirm_id, None)
        self.bus.publish(
            "confirm_resolved",
            channel,
            confirm_id=confirm_id,
            approved=approved,
            resolution=resolution,
        )
        return approved, resolution

    def resolve(self, confirm_id: str, approved: bool) -> bool:
        """Answer a pending confirmation. False if unknown or already resolved."""
        future = self._pending.get(confirm_id)
        if future is None or future.done():
            return False
        future.set_result(approved)
        return True

    def cancel_all(self) -> None:
        """Kill switch: fail every pending confirmation as refused."""
        for future in self._pending.values():
            if not future.done():
                future.set_result(False)


class SafetyGate:
    """Facade: classification + confirmation + dry-run switch."""

    def __init__(self, cfg: SafetyConfig, bus: EventBus) -> None:
        self.cfg = cfg
        self.confirmations = ConfirmationManager(bus, cfg.confirm_timeout_s)
        self.session = SafetySession()

    def classify(
        self,
        tool: str,
        kwargs: dict,
        user_text: str | None = None,
        channel: str = "",
    ) -> Verdict:
        return classify_tool(tool, kwargs, self.cfg, user_text, self.session, channel)

    def set_voice_verified(self, channel: str, verified: bool) -> None:
        """Mark a channel's current turn as owner-verified (or not)."""
        if verified:
            self.session.unverified_channels.discard(channel)
        else:
            self.session.unverified_channels.add(channel)

    def note_approval(self, tool: str, kwargs: dict) -> None:
        """Record what an approved confirmation covers for the rest of the
        session (e.g. a browser domain). No-op for tools with no session state."""
        actions = ("click", "type", "press")
        if tool == "browser_act" and str(kwargs.get("action", "")).lower() in actions:
            domain = self.session.current_browser_domain()
            if domain:
                self.session.confirmed_browser_domains.add(domain)

    @property
    def dry_run(self) -> bool:
        return self.cfg.mode == "dry_run"
