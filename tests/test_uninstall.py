r"""v6: the installer contract -- the promises the shipped build has to keep.

Started as the uninstall fix and grew into the whole surface a release can break
silently. Three groups, all DRIFT GUARDS rather than simulations:

  * The uninstaller must delete the directory Baby actually uses. Tauri's NSIS
    template deletes %LOCALAPPDATA%\<BUNDLEID> when the user ticks "delete
    application data", but Baby's state lives in %LOCALAPPDATA%\baby, a name that
    predates the bundle id. Before the installer hook, ticking that box removed
    nothing: the user's .env (with their cloud API keys), baby.db with every
    conversation, the models and the venv all survived an uninstall the user
    believed had removed them, and the shipped EULA promised otherwise.
  * The public docs must describe the installer we actually ship -- the no-admin
    claim, the uninstall checkbox, and the SmartScreen section against the real
    (absent) signing config.
  * The seven version tracks must agree, and match the CHANGELOG. They drifted
    through v6 development, and a mismatched installer filename is the kind of
    thing nobody notices until a bug report cites the wrong version.
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


# --- release version alignment ----------------------------------------------
# Seven files carry the version. They drifted silently through v6 development (the
# shell built and shipped installers stamped 5.0.0 while the project was on v6),
# and a mismatched installer is the kind of thing nobody notices until a user
# reports "I have 5.0.0" against a 6.0.0 bug.


def _versions() -> dict[str, str]:
    import re
    import tomllib

    out: dict[str, str] = {}
    out["pyproject.toml"] = tomllib.loads(
        (_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    )["project"]["version"]
    out["tauri.conf.json"] = _conf()["version"]

    cargo = (_ROOT / "ui/shell/src-tauri/Cargo.toml").read_text(encoding="utf-8")
    out["Cargo.toml"] = re.search(r'^version = "([^"]+)"', cargo, re.M).group(1)

    lock = (_ROOT / "ui/shell/src-tauri/Cargo.lock").read_text(encoding="utf-8")
    out["Cargo.lock"] = re.search(
        r'name = "baby-shell"\nversion = "([^"]+)"', lock
    ).group(1)

    for pkg in ("ui/shell/package.json", "ui/app/package.json"):
        out[pkg] = json.loads((_ROOT / pkg).read_text(encoding="utf-8"))["version"]

    # uv restamps this from pyproject on any sync, so it drifts on its own if a
    # bump lands without one.
    uvlock = (_ROOT / "uv.lock").read_text(encoding="utf-8")
    out["uv.lock"] = re.search(
        r'name = "baby"\nversion = "([^"]+)"', uvlock
    ).group(1)
    return out


def test_every_track_carries_the_same_version():
    seen = _versions()
    assert len(set(seen.values())) == 1, f"version drift: {seen}"


def test_the_shipped_version_is_v6():
    """The installer's filename comes from tauri.conf.json, so this is the number
    a user actually sees on the .exe they download."""
    assert _conf()["version"].startswith("6."), _conf()["version"]


def test_changelog_documents_the_shipped_version():
    version = _conf()["version"]
    changelog = (_ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    assert f"## v{version}" in changelog, f"CHANGELOG.md has no entry for v{version}"
