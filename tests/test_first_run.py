r"""The first-run bootstrap script, guarded where it has actually gone wrong.

`uv sync` reinstalls the openwakeword wheel, and reinstalling a wheel deletes its
package directory -- including the 19 MB of weights openWakeWord's own downloader
writes there by default. An upgrade therefore left a working install permanently
deaf: the models were gone, `setup_complete` was already true so the wizard never
re-ran, and nothing said a word. The weights now live under
`models_dir()/openwakeword` (core.paths.wakeword_dir), and this script lifts an
older install's copy out of the venv BEFORE the sync that would destroy it.

Placement is the whole point of that step, so the order guard runs everywhere; the
copy itself is exercised for real on Windows.
"""

from __future__ import annotations

import platform
import subprocess
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
_PS1 = _ROOT / "installer" / "first_run.ps1"


def _src() -> str:
    return _PS1.read_text(encoding="utf-8")


def _function(name: str) -> str:
    """The full text of one PowerShell function, brace-matched."""
    src = _src()
    start = src.index(f"function {name} {{")
    depth = 0
    for i in range(start, len(src)):
        if src[i] == "{":
            depth += 1
        elif src[i] == "}":
            depth -= 1
            if depth == 0:
                return src[start : i + 1]
    raise AssertionError(f"unbalanced braces in {name}")


def test_the_wake_models_are_rescued_before_the_sync_deletes_them():
    """After the sync there is nothing left to rescue -- order is the fix."""
    src = _src()
    rescue = src.index("\n    Save-WakeWordModels")
    sync = src.index('Invoke-Uv @("sync"')
    assert rescue < sync


def test_the_rescue_reads_the_venv_and_writes_the_data_dir():
    """Pinned both ends: a copy that reads the wrong dir or writes back into the
    venv would leave the models exactly where the next sync deletes them."""
    fn = _function("Save-WakeWordModels")
    assert 'Join-Path $VenvDir "Lib\\site-packages\\openwakeword\\resources\\models"' in fn
    assert 'Join-Path $BabyHome "models\\openwakeword"' in fn
    assert "if (Test-Path $target) { continue }" in fn  # never clobber


def test_the_script_stays_ascii_and_bom_free():
    """PowerShell 5.1 refuses a BOM'd script, and the error names anything but
    encoding. Checked as bytes, because a decoded read hides both."""
    raw = _PS1.read_bytes()
    assert not raw.startswith(b"\xef\xbb\xbf")
    assert not [b for b in raw if b > 127]


@pytest.mark.skipif(platform.system() != "Windows", reason="PowerShell 5.1 only")
def test_the_rescue_actually_moves_the_files(tmp_path):
    """Run the shipped function against a fake venv: it copies every .onnx across,
    leaves the .tflite twins (never loaded), and refuses to overwrite."""
    src = tmp_path / ".venv" / "Lib" / "site-packages" / "openwakeword" / "resources" / "models"
    src.mkdir(parents=True)
    (src / "hey_jarvis_v0.1.onnx").write_bytes(b"weights")
    (src / "melspectrogram.onnx").write_bytes(b"mel")
    (src / "hey_jarvis_v0.1.tflite").write_bytes(b"tflite")
    home = tmp_path / "home"
    dst = home / "models" / "openwakeword"
    dst.mkdir(parents=True)
    (dst / "melspectrogram.onnx").write_bytes(b"already mine")

    script = "\n".join(
        [
            f"$VenvDir = '{tmp_path / '.venv'}'",
            f"$BabyHome = '{home}'",
            "function Write-Step($m) { Write-Host $m }",
            _function("Save-WakeWordModels"),
            "Save-WakeWordModels",
        ]
    )
    ps1 = tmp_path / "rescue.ps1"
    ps1.write_text(script, encoding="ascii")
    r = subprocess.run(
        ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(ps1)],
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert r.returncode == 0, r.stderr

    assert (dst / "hey_jarvis_v0.1.onnx").read_bytes() == b"weights"
    assert not (dst / "hey_jarvis_v0.1.tflite").exists()
    assert (dst / "melspectrogram.onnx").read_bytes() == b"already mine"
    assert "Kept 1 wake-word model file(s)" in r.stdout
