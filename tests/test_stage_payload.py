r"""The staging script is the last thing between the repo and the installer.

It has no test coverage because running it stages the whole tree, so what is here
guards the three ways it has silently shipped a wrong installer:

  * A mistyped `BABY_UV_EXE` used to stage cleanly and print OK, producing an
    installer that could not build a venv on a machine without uv -- a failure that
    only appears on a stranger's first launch.
  * `ui/app/dist` was checked for EXISTENCE only. Nothing in the shell's build
    rebuilds the SPA (tauri.conf.json's beforeBuildCommand is this script), so an
    edit to `ui/app/src` that was never rebuilt shipped the previous UI. Caught
    exactly that way: a changed wizard string, absent from the staged bundle.
  * PowerShell 5.1 refuses a script with a UTF-8 BOM or stray non-ASCII, and the
    failure looks nothing like an encoding problem.

The first two are source guards -- an edit that removes the check is what
reintroduces the bug. The last one reads the actual bytes.
"""

from __future__ import annotations

from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
_PS1 = _ROOT / "scripts" / "stage_payload.ps1"


def _src() -> str:
    return _PS1.read_text(encoding="utf-8")


def test_a_mistyped_uv_path_fails_the_build():
    """Asking for uv.exe and not getting it is an error, not a warning."""
    src = _src()
    assert "throw" in src.split("if ($UvExe)", 1)[1].split("} else {", 1)[0]
    assert "Test-Path $UvExe" in src


def test_a_stale_spa_bundle_fails_the_build():
    """Built once, ever, is not the same as built from this source.

    Each assertion is a separate way to lose the check. An earlier version of this
    test looked for "LastWriteTimeUtc" anywhere in the file, which stayed true with
    the baseline read gutted -- the string still appeared in the comparison that no
    longer had anything to compare against.
    """
    block = _src().split("$distIndex = ", 1)[1].split("Copy-Tree", 1)[0]
    assert "(Get-Item $distIndex).LastWriteTimeUtc" in block, "dist's own age is never read"
    assert "ui\\app\\src" in block, "nothing is compared against the SPA source"
    assert "-gt $distTime" in block, "the two are never actually compared"
    assert "STALE" in block and "throw" in block, "a stale bundle does not fail the build"


def test_the_script_stays_ascii_without_a_bom():
    """Windows PowerShell 5.1 is the interpreter here, and it is unforgiving about
    both. This has broken a release build before."""
    raw = _PS1.read_bytes()
    assert not raw.startswith(b"\xef\xbb\xbf"), "UTF-8 BOM: PS 5.1 will not run this"
    bad = [(i, raw[i]) for i in range(len(raw)) if raw[i] > 127]
    assert not bad, f"non-ASCII bytes at {bad[:3]}"
