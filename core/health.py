"""v6 W3: the first-run FUNCTIONAL health check.

A green `uv sync` is not proof a stranger's Baby works: a native wheel can install
yet fail to dlopen (missing VC++ runtime, wrong ABI), and a model file can be absent
even when its wheel imports fine. This module imports each dependency AND does a
trivial real operation, so a break surfaces here -- during first-run, with a
plain-language fix -- not three screens later when the user tries to talk to Baby.

It ports the throwaway W0 spike (`spike/backend_delivery/health_probe.py`, git
5c1268d) and adds the model-LOAD probes the spike explicitly deferred to W3
(whisper transcribe, kokoro synth, e5 encode, wake-word detect, speaker embed,
Ollama warm-ping). Every heavy import is lazy, so `import core.health` is cheap and
safe even where a wheel isn't installed yet.

Two levels:
* ``wheels`` -- native-wheel import + real-op checks. Run right after `uv sync`,
  before any model is downloaded.
* ``full`` -- also the model-load checks. Run after the models land.

Mode-aware: the Ollama daemon + 9B checks apply only to a Full install; a cloud-only
install skips them entirely (they aren't listed, so they can't fail).

Run standalone with the target venv's python (the first-run harness does this):

    <venv>\\Scripts\\python.exe -m core.health --mode full --json [--browser]
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import sys
from collections.abc import Callable
from dataclasses import asdict, dataclass

# A check returns (status, detail); "pass"/"fail" decide required-gating, "skip" is a
# non-failure (no GPU, optional asset absent, headless VM).
Check = tuple[str, bool, Callable[[], "tuple[str, str]"]]

_OLLAMA = os.environ.get("OLLAMA_HOST", "http://127.0.0.1:11434").rstrip("/")
_DAILY_MODEL = "qwen3.5:9b-q4_K_M"


@dataclass
class Result:
    name: str
    required: bool
    ok: bool
    status: str  # "pass" | "fail" | "skip"
    detail: str


def _short(exc: BaseException, limit: int = 240) -> str:
    """One-line, truncated error -- never a raw multi-line traceback to a user."""
    msg = f"{type(exc).__name__}: {exc}".replace("\n", " ").strip()
    return msg if len(msg) <= limit else msg[: limit - 1] + "…"


def _preload_onnxruntime() -> None:
    """Load the venv's onnxruntime.dll before any check imports sherpa-onnx.

    sherpa-onnx loads 'onnxruntime.dll' by NAME, and Windows resolves that to the
    stale System32 WindowsML copy (ORT 1.17) first -- which segfaults sherpa (it
    needs ORT C-API >= 24). Loading the venv copy explicitly first means the
    already-resident module wins name resolution. Mirrors
    voice.speaker._preload_onnxruntime_dll; run once, before the checks.
    """
    import ctypes
    from pathlib import Path

    try:
        import onnxruntime

        dll = Path(onnxruntime.__file__).parent / "capi" / "onnxruntime.dll"
        if dll.exists():
            ctypes.WinDLL(str(dll))
    except Exception:  # noqa: BLE001 -- best-effort; sherpa raises clearly if it fails
        pass


# --- native-wheel checks (level "wheels", post-sync) ------------------------


def check_backend_imports() -> tuple[str, str]:
    import aiosqlite  # noqa: F401
    import fastapi  # noqa: F401
    import openai  # noqa: F401
    import uvicorn  # noqa: F401
    import yaml  # noqa: F401

    return "pass", "fastapi/uvicorn/openai/aiosqlite/pyyaml import ok"


def check_torch() -> tuple[str, str]:
    import torch

    s = float(torch.tensor([1.0, 2.0, 3.0]).sum())
    assert s == 6.0, f"tensor sum wrong: {s}"
    return "pass", f"torch {torch.__version__} (cuda={torch.cuda.is_available()}), sum ok"


def check_ctranslate2() -> tuple[str, str]:
    import ctranslate2

    n = ctranslate2.get_cuda_device_count()  # loads the native lib
    return "pass", f"ctranslate2 {ctranslate2.__version__}, cuda_devices={n}"


def check_onnxruntime() -> tuple[str, str]:
    import onnxruntime as ort

    providers = ort.get_available_providers()
    assert "CPUExecutionProvider" in providers, f"no CPU provider: {providers}"
    return "pass", f"onnxruntime {ort.__version__}, providers ok"


def check_sqlite_vec() -> tuple[str, str]:
    import sqlite3

    import sqlite_vec

    conn = sqlite3.connect(":memory:")
    try:
        conn.enable_load_extension(True)
        sqlite_vec.load(conn)  # loads the native vec0 extension
        (ver,) = conn.execute("SELECT vec_version()").fetchone()
        return "pass", f"sqlite-vec loaded, vec_version={ver}"
    finally:
        conn.close()


def check_voice_wheels() -> tuple[str, str]:
    # Import-only for the wheels whose native runtime is exercised by the model-load
    # checks below (kokoro synth, whisper transcribe, wake detect).
    import faster_whisper  # noqa: F401
    import kokoro_onnx  # noqa: F401
    import openwakeword  # noqa: F401
    import sherpa_onnx  # noqa: F401
    import silero_vad  # noqa: F401

    return "pass", "faster_whisper/kokoro_onnx/sherpa_onnx/openwakeword/silero_vad import ok"


def check_playwright_import() -> tuple[str, str]:
    import playwright  # noqa: F401

    ver = getattr(playwright, "__version__", "?")
    return "pass", f"playwright {ver} import ok (browser launch is the optional check)"


# --- VC++ runtime (level "wheels") ------------------------------------------


def check_vcredist() -> tuple[str, str]:
    """The MSVC runtime every native wheel dlopens. On non-Windows this is n/a. The
    honest load proof is the onnxruntime real-op above; this adds the direct DLL
    presence check so a missing runtime maps to a plain 'install VC++' message."""
    if platform.system() != "Windows":
        return "skip", "not Windows -- VC++ runtime n/a"
    system32 = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32")
    needed = ("vcruntime140.dll", "vcruntime140_1.dll", "msvcp140.dll")
    missing = [d for d in needed if not os.path.exists(os.path.join(system32, d))]
    if missing:
        return "fail", (
            f"missing MSVC runtime DLL(s): {', '.join(missing)} -- install the Visual "
            "C++ 2015-2022 x64 runtime (aka.ms/vs/17/release/vc_redist.x64.exe)"
        )
    return "pass", "vcruntime140/vcruntime140_1/msvcp140 present"


# --- model-load checks (level "full", post-download) ------------------------


def check_whisper() -> tuple[str, str]:
    import numpy as np

    from voice.stt import SpeechToText

    stt = SpeechToText()
    stt.load()  # constructs WhisperModel (auto-downloads if absent)
    # ~0.5 s of a quiet 220 Hz tone, int16 mono 16 kHz (> the 0.3 s min-speech guard).
    t = np.arange(8000, dtype=np.float32) / 16000.0
    buf = (np.sin(2 * np.pi * 220 * t) * 800).astype(np.int16)
    text, lang = stt.transcribe(buf)  # exercises the real CT2 decode path
    return "pass", f"whisper loaded + transcribed (lang={lang or '-'})"


def check_kokoro() -> tuple[str, str]:
    from core import paths
    from voice.tts import TextToSpeech

    tts = TextToSpeech(
        model_path=str(paths.resolve_model("models/kokoro-v1.0.onnx")),
        voices_path=str(paths.resolve_model("models/voices-v1.0.bin")),
    )
    tts.load()  # raises FileNotFoundError if the model/voices weren't fetched
    pcm, sr = tts.synth("Baby ready")
    assert pcm is not None and len(pcm) > 0, "kokoro produced empty PCM"
    return "pass", f"kokoro synthesized {len(pcm)} samples @ {sr} Hz (espeak-ng ok)"


def check_embedder() -> tuple[str, str]:
    import asyncio

    from memory.embedder import DIMENSIONS, Embedder

    emb = Embedder()
    vec = asyncio.run(emb.embed_query("ping"))
    assert len(vec) == DIMENSIONS, f"e5 dim {len(vec)} != {DIMENSIONS}"
    return "pass", f"e5 embedder encoded ok, dim={len(vec)}"


def check_wakeword() -> tuple[str, str]:
    import numpy as np

    from voice.wakeword import WakeWord

    w = WakeWord(builtin_fallback="hey_jarvis")
    w.load()  # raises if openWakeWord assets weren't downloaded
    fired = w.detected(np.zeros(1280, dtype=np.int16))  # one 80 ms chunk
    assert isinstance(fired, bool), f"detected() returned {type(fired)}"
    return "pass", "openWakeWord loaded + ran predict on one chunk"


def check_speaker() -> tuple[str, str]:
    # Optional: skip cleanly if the CAM++ model wasn't fetched (verify is off until
    # the owner enrolls anyway).
    from pathlib import Path

    import numpy as np

    from core import paths

    model = paths.resolve_model("models/wespeaker_en_voxceleb_CAM++.onnx")
    if not Path(model).exists():
        return "skip", "CAM++ speaker model not present (optional -- off until enrollment)"
    from voice.speaker import SherpaExtractor

    vec = SherpaExtractor(str(model)).embed(np.zeros(8000, dtype=np.int16))
    assert len(vec) == 512, f"speaker embedding dim {len(vec)} != 512"
    return "pass", f"CAM++ speaker embedder ok, dim={len(vec)}"


def check_ollama() -> tuple[str, str]:
    import httpx

    r = httpx.get(f"{_OLLAMA}/api/tags", timeout=5.0)
    r.raise_for_status()
    n = len(r.json().get("models", []))
    return "pass", f"Ollama daemon up at {_OLLAMA} ({n} models present)"


def check_ollama_model() -> tuple[str, str]:
    import httpx

    tags = httpx.get(f"{_OLLAMA}/api/tags", timeout=5.0)
    tags.raise_for_status()
    names = [m.get("name", "") for m in tags.json().get("models", [])]
    if _DAILY_MODEL not in names:
        return "fail", f"{_DAILY_MODEL} not pulled (Full mode needs it) -- run the model pull"
    # Functional warm-ping: 1 token loads the weights into VRAM and proves it answers.
    body = {
        "model": _DAILY_MODEL,
        "messages": [{"role": "user", "content": "ping"}],
        "max_tokens": 1,
        "stream": False,
    }
    resp = httpx.post(f"{_OLLAMA}/v1/chat/completions", json=body, timeout=120.0)
    resp.raise_for_status()
    return "pass", f"{_DAILY_MODEL} present + answered a 1-token warm-ping"


# --- optional / soft checks (level "full") ----------------------------------


def check_pynvml() -> tuple[str, str]:
    import pynvml

    try:
        pynvml.nvmlInit()
    except Exception as exc:  # noqa: BLE001
        return "skip", f"no NVIDIA GPU / NVML init failed ({_short(exc)}) -- cloud-only ok"
    try:
        if pynvml.nvmlDeviceGetCount() == 0:
            return "skip", "NVML up but 0 devices -- cloud-only ok"
        h = pynvml.nvmlDeviceGetHandleByIndex(0)
        name = pynvml.nvmlDeviceGetName(h)
        name = name.decode() if isinstance(name, bytes) else name
        gb = pynvml.nvmlDeviceGetMemoryInfo(h).total / (1024**3)
        return "pass", f"GPU0 {name}, {gb:.1f} GB VRAM"
    finally:
        pynvml.nvmlShutdown()


def check_sounddevice() -> tuple[str, str]:
    import sounddevice as sd

    try:
        devices = sd.query_devices()
    except Exception as exc:  # noqa: BLE001
        return "skip", f"no audio device ({_short(exc)}) -- mic setup deferred"
    n = len(devices) if devices is not None else 0
    return "pass", f"sounddevice ok, {n} audio devices"


def check_chromium() -> tuple[str, str]:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        try:
            browser = p.chromium.launch(headless=True)
        except Exception as exc:  # noqa: BLE001
            return "skip", f"chromium not installed ({_short(exc)}); run playwright install"
        try:
            page = browser.new_page()
            page.set_content("<title>baby-probe</title><h1>ok</h1>")
            assert page.title() == "baby-probe"
            return "pass", "chromium launched + rendered + read title back"
        finally:
            browser.close()


def check_sensors() -> tuple[str, str]:
    from tools.sensors import get_sensors

    data = get_sensors()
    if isinstance(data, dict) and "error" in data:
        return "skip", f"sensors unavailable ({data.get('error', '')[:80]}) -- optional"
    return "pass", "LibreHardwareMonitor sensors reachable"


# --- check tables + runner --------------------------------------------------


def wheel_checks() -> list[Check]:
    """Native-wheel import + real-op checks. Safe to run right after `uv sync`."""
    return [
        ("backend-imports", True, check_backend_imports),
        ("torch", True, check_torch),
        ("ctranslate2", True, check_ctranslate2),
        ("onnxruntime", True, check_onnxruntime),
        ("sqlite-vec", False, check_sqlite_vec),  # soft: brute-force BLOB fallback
        ("voice-wheels", True, check_voice_wheels),
        ("playwright-import", True, check_playwright_import),
        ("vcredist", True, check_vcredist),
    ]


def model_checks(mode: str) -> list[Check]:
    """Model-LOAD checks. Run after the assets land. Ollama entries only for Full."""
    checks: list[Check] = [
        ("whisper", True, check_whisper),
        ("kokoro", True, check_kokoro),
        ("embedder", True, check_embedder),
        ("wakeword", True, check_wakeword),
        ("speaker", False, check_speaker),
    ]
    if mode == "full":
        checks.append(("ollama", True, check_ollama))
        checks.append(("ollama-model", True, check_ollama_model))
    return checks


def optional_checks(with_browser: bool) -> list[Check]:
    checks: list[Check] = [
        ("gpu", False, check_pynvml),
        ("audio", False, check_sounddevice),
        ("sensors", False, check_sensors),
    ]
    if with_browser:
        checks.append(("chromium", False, check_chromium))
    return checks


def run(checks: list[Check]) -> list[Result]:
    """Run a check list; a check that raises becomes a 'fail' Result (never crashes)."""
    results: list[Result] = []
    for name, required, fn in checks:
        try:
            status, detail = fn()
        except Exception as exc:  # noqa: BLE001 -- the probe must never itself crash
            status, detail = "fail", _short(exc)
        results.append(Result(name, required, status != "fail", status, detail))
    return results


def overall_ok(results: list[Result]) -> bool:
    """True iff no REQUIRED check failed (soft/optional 'skip'/'fail' don't gate)."""
    return not any(r.required and not r.ok for r in results)


def run_all(mode: str = "full", level: str = "full", with_browser: bool = False) -> list[Result]:
    # Bind the venv's ORT before any check imports sherpa-onnx, or the speaker probe
    # segfaults on System32's stale onnxruntime.dll (see _preload_onnxruntime).
    _preload_onnxruntime()
    checks = wheel_checks()
    if level == "full":
        checks += model_checks(mode)
        checks += optional_checks(with_browser)
    return run(checks)


def readiness_summary(results: list[Result]) -> str:
    """Plain-language report: on failure, names the exact broken dep(s) + the fix."""
    failed = [r for r in results if r.required and not r.ok]
    if not failed:
        skipped = [r.name for r in results if r.status == "skip"]
        tail = f" (optional skipped: {', '.join(skipped)})" if skipped else ""
        return "Baby is ready -- all required components work." + tail
    lines = ["Baby isn't ready yet. These need attention:"]
    lines += [f"  - {r.name}: {r.detail}" for r in failed]
    return "\n".join(lines)


def _report_text(results: list[Result]) -> str:
    glyph = {"pass": "PASS", "fail": "FAIL", "skip": "skip"}
    lines = [f"Baby health check  (python {sys.version.split()[0]}, {platform.system()})", "-" * 72]
    for r in results:
        tag = "" if r.required else " (optional)"
        lines.append(f"  [{glyph[r.status]}] {r.name}{tag}: {r.detail}")
    lines.append("-" * 72)
    lines.append(readiness_summary(results))
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description="Baby first-run functional health check")
    ap.add_argument("--mode", choices=("full", "cloud_only"), default="cloud_only")
    ap.add_argument("--level", choices=("wheels", "full"), default="full")
    ap.add_argument("--browser", action="store_true", help="also attempt a real Chromium launch")
    ap.add_argument("--json", action="store_true", help="machine-readable report")
    args = ap.parse_args()

    results = run_all(mode=args.mode, level=args.level, with_browser=args.browser)
    ok = overall_ok(results)
    if args.json:
        print(json.dumps({
            "ok": ok,
            "mode": args.mode,
            "level": args.level,
            "python": sys.version.split()[0],
            "platform": platform.platform(),
            "results": [asdict(r) for r in results],
            "summary": readiness_summary(results),
        }, indent=2))
    else:
        print(_report_text(results))
    return 0 if ok else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(130)
