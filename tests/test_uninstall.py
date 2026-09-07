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
import os
import platform
import re
import shutil
import subprocess
import time
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
_HOOKS = _ROOT / "ui" / "shell" / "src-tauri" / "installer_hooks.nsh"
_CONF = _ROOT / "ui" / "shell" / "src-tauri" / "tauri.conf.json"
_MAIN_RS = _ROOT / "ui" / "shell" / "src-tauri" / "src" / "main.rs"
_EULA = _ROOT / "installer" / "EULA.txt"


def _conf() -> dict:
    return json.loads(_CONF.read_text(encoding="utf-8"))


def _hook_code() -> str:
    """The hook with its `;` comments stripped. Ordering assertions below compare
    offsets, and the comments discuss the very instructions they gate -- so without
    this a comment mentioning RmDir reads as a delete that precedes its own guard."""
    hook = _HOOKS.read_text(encoding="utf-8")
    return "\n".join(ln for ln in hook.splitlines() if not ln.lstrip().startswith(";"))


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
    # The delete is inside the guard, not before it. Code only: the comments name
    # these instructions while explaining them, which is not an ordering violation.
    code = _hook_code()
    guard = code.index("$DeleteAppDataCheckboxState")
    assert guard < code.index("RmDir")


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


def _license_path() -> Path | None:
    for name in ("LICENSE", "LICENSE.md", "LICENSE.txt"):
        p = _ROOT / name
        if p.exists():
            return p
    return None


def test_signing_doc_tracks_the_license_state_both_ways():
    """SignPath's free tier needs an OSI-approved license.

    Two-way guard, because either half going stale misleads the release process:
    with no LICENSE the blocker must stay written down, and WITH one the doc must
    stop calling it a blocker -- a resolved blocker left in the docs is how a
    project sits on a signing application it could already have made.
    """
    signing = _SIGNING_DOC.read_text(encoding="utf-8")
    assert "SignPath" in signing
    if _license_path() is None:
        assert "Blocker" in signing, "the LICENSE blocker must stay documented"
    else:
        assert "Blocker" not in signing, (
            "a LICENSE exists, but SIGNING.md still calls it a blocker"
        )


def test_the_license_is_osi_approved_and_attributed():
    """An unattributed or non-OSI license fails a SignPath application, and MIT
    is what the shipped EULA's no-warranty terms assume."""
    path = _license_path()
    assert path is not None, "public release needs a LICENSE"
    text = path.read_text(encoding="utf-8")
    assert "MIT License" in text
    assert "Permission is hereby granted, free of charge" in text
    assert 'THE SOFTWARE IS PROVIDED "AS IS"' in text
    assert re.search(r"Copyright \(c\) \d{4} \S+", text), "no copyright holder line"


def test_readme_and_packaging_state_the_license():
    """A LICENSE nobody is pointed at does not tell a user what they may do."""
    readme = (_ROOT / "README.md").read_text(encoding="utf-8")
    assert "## License" in readme and "MIT" in readme
    pyproject = (_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert 'license = "MIT"' in pyproject


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


def test_the_docs_hand_out_the_shipped_installers_filename():
    """The checksum commands quote a filename, and a stale one is worse than no
    command at all: `Get-FileHash` on a name that is not on disk fails, and the
    reader cannot tell a typo from a tampered download. These went stale the moment
    the version moved, so pin them to the version the .exe will actually carry.

    Deliberately scoped: section 2 of the checklist names the 6.0.0 installer on
    purpose, because that is the build the clean-VM matrix was measured against.
    A record of what was tested is not drift.
    """
    import re

    version = _conf()["version"]
    checklist = (_ROOT / "tests/manual/v6_release_checklist.md").read_text(
        encoding="utf-8"
    )
    sources = {
        "docs/INSTALL.md": (_ROOT / "docs/INSTALL.md").read_text(encoding="utf-8"),
        "docs/SIGNING.md": (_ROOT / "docs/SIGNING.md").read_text(encoding="utf-8"),
        # The build section only -- see the docstring.
        "v6_release_checklist.md §1": checklist.split("## 1. Build the artifact", 1)[
            1
        ].split("## 2.", 1)[0],
    }

    stale = {}
    for where, text in sources.items():
        wrong = [
            v
            for v in re.findall(r"Baby_(\d+\.\d+\.\d+)_x64-setup\.exe", text)
            if v != version
        ]
        if wrong:
            stale[where] = wrong
    assert not stale, f"docs quote an installer that is not v{version}: {stale}"
    assert any(
        f"Baby_{version}_x64-setup.exe" in text for text in sources.values()
    ), "no doc quotes the shipped installer filename at all"


def test_removal_is_gated_on_a_directory_an_install_provisioned():
    r"""%LOCALAPPDATA%\baby is not exclusive to an installed build.

    core/paths.py resolves the logs, browser profile and screenshot caches there for
    a DEV checkout too, "untouched" by BABY_HOME. An unguarded RmDir /r therefore
    takes a developer's browser profile and file index with it on any machine that
    also runs Baby from source. The venv is the one marker only an install creates in
    that directory -- a checkout keeps its own in the repo.

    This cannot make the shared case safe (with both present the venv exists and
    everything still goes); it removes the case where an uninstaller wipes a
    directory no install ever owned. The manual checklist carries the rest.
    """
    hook = _HOOKS.read_text(encoding="utf-8")
    marker = re.search(r'IfFileExists\s+"([^"]+)"', hook)
    assert marker, "the deletion is not gated on an install marker"
    assert marker.group(1).lower().startswith(r"$localappdata\baby\.venv")
    # The gate must precede the delete, not sit after it.
    code = _hook_code()
    assert code.index("IfFileExists") < code.index("RmDir")


def test_the_checklist_warns_where_the_hook_cannot_help():
    """The ticked branch is a checklist instruction. On a dev box it destroys the
    shared caches, and the hook cannot detect that -- so the warning is the only
    thing standing between the owner and their own browser profile."""
    doc = (_ROOT / "tests" / "manual" / "v6_release_checklist.md").read_text(encoding="utf-8")
    section = doc.split("## 5. Uninstall", 1)[1].split("## 6.", 1)[0]
    # The blockquote specifically -- the phrase also occurs in a checklist item
    # below, which would let the warning itself be deleted unnoticed.
    warning = "\n".join(ln for ln in section.splitlines() if ln.lstrip().startswith(">"))
    assert "from source" in warning, "no warning about a shared data dir"
    assert "robocopy" in warning, "the warning gives no backup command"


# --- the guard that keeps an upgrade from wiping the user's data -------------
# Reported twice from the author's own machine during v6 testing: a double-clicked
# newer setup.exe came back to an empty data directory -- no keys, no conversations,
# `setup_complete` false, the models downloading again. The delete was already behind
# two guards and still fired, because `$UpdateMode <> 1` does not mean "not
# upgrading". NSIS performs an upgrade by running the OLD uninstaller first, with a
# plain ExecWait and no /UPDATE, so $UpdateMode is 0 and the confirm page offers
# "Delete application data" to someone who believes they are upgrading.


def _reinstall_guard() -> list[str]:
    """The guard lines themselves, lifted out of the shipped hook.

    The executable test below compiles these verbatim, so it exercises what ships
    rather than a paraphrase of it.
    """
    code = _hook_code().splitlines()
    first = next((i for i, ln in enumerate(code) if "GetFullPathName" in ln), None)
    assert first is not None, "the hook no longer resolves $EXEDIR against $INSTDIR"
    last = next(
        (i for i in range(first, len(code)) if code[i].lstrip().startswith("${If}")),
        None,
    )
    assert last is not None, "the resolved paths are never compared"
    return [ln.strip() for ln in code[first : last + 1]]


def test_a_reinstall_cannot_reach_the_delete():
    """`_?=` is what an installer passes when it ExecWaits on the old uninstaller,
    and NSIS strips it out of $CMDLINE before the script sees it -- so the flag is
    unreadable. What it does is not: it suppresses the copy to the temp directory.
    Running in place therefore means a parent is waiting, which means a reinstall."""
    guard = _reinstall_guard()
    assert any("$EXEDIR" in ln for ln in guard), "nothing distinguishes a reinstall"
    assert any("$INSTDIR" in ln for ln in guard)
    code = _hook_code()
    assert code.index("$EXEDIR") < code.index("RmDir"), "the guard is after the delete"


def test_update_mode_alone_is_not_trusted():
    """Both guards have to be there. $UpdateMode still covers the built-in updater's
    /UPDATE path; the double-click upgrade is the one it never saw."""
    code = _hook_code()
    assert "$UpdateMode <> 1" in code
    assert "$EXEDIR" in code, "$UpdateMode is doing this on its own again"


def _makensis() -> str | None:
    found = shutil.which("makensis.exe")
    if found:
        return found
    local = os.environ.get("LOCALAPPDATA")
    roots = [Path.home() / "AppData" / "Local"] + ([Path(local)] if local else [])
    for root in roots:
        candidate = root / "tauri" / "NSIS" / "makensis.exe"
        if candidate.exists():
            return str(candidate)
    return None


_PROBE = """!include LogicLib.nsh
Name "baby-reinstall-guard-probe"
OutFile "maker.exe"
InstallDir "$EXEDIR\\inst"
SilentInstall silent
SilentUnInstall silent
RequestExecutionLevel user

Section
  SetOutPath "$INSTDIR"
  WriteUninstaller "$INSTDIR\\un.exe"
SectionEnd

Section Uninstall
{guard}
    StrCpy $R6 "DELETE"
  ${{Else}}
    StrCpy $R6 "KEEP"
  ${{EndIf}}
  FileOpen $R7 "{out}" w
  FileWrite $R7 "$R6"
  FileClose $R7
SectionEnd
"""


def _verdict(path: Path, seconds: float = 30.0) -> str:
    """The un-`_?=` run detaches into the temp directory, so the parent process
    returns before the copy has written anything."""
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if path.exists():
            text = path.read_text(encoding="ascii").strip()
            if text:
                return text
        time.sleep(0.25)
    return "<no verdict>"


@pytest.mark.skipif(platform.system() != "Windows", reason="NSIS is Windows-only")
@pytest.mark.skipif(_makensis() is None, reason="makensis.exe not installed")
def test_the_reinstall_guard_holds_against_real_nsis(tmp_path):
    r"""Compile the shipped guard and run both flows for real.

    A static assertion cannot catch what actually threatens this guard, which is
    NSIS changing its mind about when it copies the uninstaller aside. Measured on
    the NSIS this project builds with: a standalone run reports $EXEDIR under
    %TEMP%\~nsu.tmp while $INSTDIR stays the install directory, and a run given
    `_?=` reports the two as equal.
    """
    verdict_file = tmp_path / "verdict.txt"
    script = tmp_path / "probe.nsi"
    script.write_text(
        _PROBE.format(
            guard="\n".join("  " + ln for ln in _reinstall_guard()),
            out=str(verdict_file),
        ),
        encoding="utf-8",
    )
    built = subprocess.run(
        [_makensis(), "probe.nsi"], cwd=tmp_path, capture_output=True, text=True
    )
    assert built.returncode == 0, built.stdout + built.stderr

    maker, inst = tmp_path / "maker.exe", tmp_path / "inst"

    # A reinstall: the installer ExecWaits on the old uninstaller, with `_?=`.
    subprocess.run([str(maker)], cwd=tmp_path, timeout=180, check=True)
    subprocess.run(
        [str(inst / "un.exe"), "_?=" + str(inst)],
        cwd=tmp_path,
        timeout=180,
        check=True,
    )
    assert _verdict(verdict_file) == "KEEP", (
        "an upgrade would delete the user's keys and conversations"
    )

    # A real uninstall: Add/Remove Programs, the Start Menu entry, or a
    # double-clicked uninstall.exe -- none of them pass `_?=`.
    verdict_file.unlink()
    subprocess.run([str(maker)], cwd=tmp_path, timeout=180, check=True)
    subprocess.run([str(inst / "un.exe")], cwd=tmp_path, timeout=180, check=True)
    assert _verdict(verdict_file) == "DELETE", (
        "the checkbox stopped deleting anything, which is the bug the hook fixed"
    )


def test_install_doc_says_an_upgrade_keeps_the_data():
    """The checkbox now behaves differently depending on how the uninstaller was
    reached. Anyone who wants a clean slate has to be told the route that works."""
    doc = _INSTALL_DOC.read_text(encoding="utf-8")
    section = doc.split("## Uninstalling", 1)[1]
    assert "upgrad" in section.lower(), "the upgrade behaviour is undocumented"
    assert "clean slate" in section.lower()


def test_the_checklist_covers_the_upgrade_branch():
    doc = (_ROOT / "tests" / "manual" / "v6_release_checklist.md").read_text(
        encoding="utf-8"
    )
    section = doc.split("## 5. Uninstall", 1)[1].split("## 6.", 1)[0]
    assert "upgrade" in section.lower(), "no upgrade-path check"
    assert "survive" in section.lower()
