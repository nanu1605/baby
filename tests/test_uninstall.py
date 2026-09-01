"""v6 W5: the uninstaller must delete the data directory Baby actually uses.

Tauri's NSIS template deletes %LOCALAPPDATA%\\<BUNDLEID> when the user ticks
"delete application data" -- but Baby's state lives in %LOCALAPPDATA%\\baby, a
name that predates the bundle id. Before the installer hook, ticking that box
removed nothing: the user's .env (with their cloud API keys), baby.db with every
conversation, the models and the venv all survived an uninstall the user believed
had removed them, and the shipped EULA promised otherwise.

These tests are a DRIFT GUARD, not a simulation of NSIS. They pin the three
things that can silently break the fix: the hook still exists and is wired into
the bundle config, it targets the same directory the shell points BABY_HOME at,
and it stays behind the user's explicit opt-in.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
_HOOKS = _ROOT / "ui" / "shell" / "src-tauri" / "installer_hooks.nsh"
_CONF = _ROOT / "ui" / "shell" / "src-tauri" / "tauri.conf.json"
_MAIN_RS = _ROOT / "ui" / "shell" / "src-tauri" / "src" / "main.rs"
_EULA = _ROOT / "installer" / "EULA.txt"


def _conf() -> dict:
    return json.loads(_CONF.read_text(encoding="utf-8"))


def test_hook_is_wired_into_the_bundle():
    """An unreferenced .nsh is dead code -- the box would go back to lying."""
    nsis = _conf()["bundle"]["windows"]["nsis"]
    assert nsis.get("installerHooks"), "installerHooks missing from tauri.conf.json"
    referenced = (_CONF.parent / nsis["installerHooks"]).resolve()
    assert referenced == _HOOKS.resolve()
    assert _HOOKS.exists()


def test_hook_deletes_the_directory_the_shell_actually_uses():
    """The whole bug was a mismatch between what NSIS deletes and where the data
    is. If either side is renamed, this fails instead of silently orphaning the
    user's keys and history again."""
    # What the shell points BABY_HOME at.
    rs = _MAIN_RS.read_text(encoding="utf-8")
    m = re.search(r'PathBuf::from\(p\)\.join\("([^"]+)"\)', rs)
    assert m, "could not find the shell's LOCALAPPDATA subdirectory"
    shell_dir = m.group(1)

    # What the uninstaller removes.
    hook = _HOOKS.read_text(encoding="utf-8")
    removed = re.findall(r'RmDir\s+/r\s+"([^"]+)"', hook)
    assert removed, "the hook removes nothing"
    assert [r.lower() for r in removed] == [f"$localappdata\\{shell_dir}".lower()], (
        f"hook removes {removed}, but the shell stores data in "
        f"%LOCALAPPDATA%\\{shell_dir}"
    )


def test_removal_stays_behind_the_users_opt_in():
    """Deleting a user's conversations and keys on every uninstall would be far
    worse than the bug this fixes. It must stay gated on the checkbox, and must
    not fire during an update (which reuses the same data dir)."""
    hook = _HOOKS.read_text(encoding="utf-8")
    assert "$DeleteAppDataCheckboxState = 1" in hook
    assert "$UpdateMode <> 1" in hook
    # The delete is inside the guard, not before it.
    guard = hook.index("$DeleteAppDataCheckboxState")
    assert guard < hook.index("RmDir")


def test_hook_only_removes_babys_own_directory():
    """A stray RmDir /r on a broader path (%LOCALAPPDATA% itself, a user profile)
    would be catastrophic and unrecoverable."""
    hook = _HOOKS.read_text(encoding="utf-8")
    for target in re.findall(r'RmDir\s+/r\s+"([^"]+)"', hook):
        assert target.startswith("$LOCALAPPDATA\\"), target
        leaf = target.split("\\", 1)[1]
        assert leaf and "\\" not in leaf.rstrip("\\"), f"too broad: {target}"
        assert leaf.strip().lower() not in ("", ".", "..", "*")


def test_eula_promise_matches_what_the_uninstaller_does():
    """Section 5 tells the user their data is deleted on an opt-in uninstall. That
    sentence is only true while the hook above exists."""
    eula = _EULA.read_text(encoding="utf-8").lower()
    assert "uninstall" in eula
    assert _HOOKS.exists(), "the EULA promises a deletion the installer no longer does"


# --- the public docs must describe the installer we actually ship ------------

_INSTALL_DOC = _ROOT / "docs" / "INSTALL.md"
_SIGNING_DOC = _ROOT / "docs" / "SIGNING.md"


def test_install_doc_matches_the_install_mode_we_ship():
    """INSTALL.md tells a stranger the install needs no administrator rights.
    That is only true while the bundle stays per-user."""
    assert _conf()["bundle"]["windows"]["nsis"]["installMode"] == "currentUser"
    doc = _INSTALL_DOC.read_text(encoding="utf-8")
    assert "per-user" in doc and "no administrator rights" in doc


def test_install_doc_documents_the_uninstall_checkbox_accurately():
    doc = _INSTALL_DOC.read_text(encoding="utf-8")
    assert "Delete application data" in doc
    assert r"%LOCALAPPDATA%\baby" in doc
    # The unticked branch matters too -- it is why a reinstall keeps history.
    assert "Unticked" in doc


def test_install_doc_is_honest_about_smartscreen():
    """The build is unsigned. A public install guide that omits the blue warning
    box leaves a first-time user assuming the download is malicious."""
    doc = _INSTALL_DOC.read_text(encoding="utf-8")
    assert "SmartScreen" in doc
    assert "not code-signed" in doc
    assert "More info" in doc and "Run anyway" in doc
    # And it must point at how to actually verify the download instead.
    assert "SHA256" in doc and "Get-FileHash" in doc
    assert _conf()["bundle"]["windows"]["nsis"].get("signCommand") is None


def test_signing_doc_records_the_license_blocker():
    """SignPath's free tier needs an OSI license. While the repo has none, that
    has to stay written down where the release process will see it."""
    signing = _SIGNING_DOC.read_text(encoding="utf-8")
    assert "SignPath" in signing
    has_license = (_ROOT / "LICENSE").exists() or (_ROOT / "LICENSE.md").exists()
    if not has_license:
        assert "Blocker" in signing, "the LICENSE blocker must stay documented"
