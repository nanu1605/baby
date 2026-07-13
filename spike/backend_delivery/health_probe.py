"""W0 backend-delivery spike — post-sync FUNCTIONAL health probe.

The crux of the v6 installer is standing up a working Python backend on a
stranger's machine via `uv sync`. A green `uv sync` is NOT proof: a native wheel
can install yet fail to load (missing VC++ redist, wrong ABI, broken .pyd). This
probe is the honest check — it *imports each native wheel and does a trivial real
operation*, so a broken dlopen surfaces here, not three screens later when a user
tries to talk to Baby.

Run it with the target interpreter after `uv sync`:

    <venv>\\Scripts\\python.exe spike/backend_delivery/health_probe.py [--browser] [--json]

Exit code is 0 only if every REQUIRED check passes. `--browser` additionally
attempts a real Chromium launch (needs `playwright install chromium` first).
`--json` emits a machine-readable report (this shape feeds W3's first-run health
check and its plain-language readiness report).

Nothing here is throwaway logic: the check table ports directly into W3.
"""

from __future__ import annotations

import argparse
import json
import platform
import sys
import traceback
from dataclasses import asdict, dataclass


@dataclass
class Result:
    name: str
    required: bool
    ok: bool
    status: str  # "pass" | "fail" | "skip"
    detail: str


def _short(exc: BaseException, limit: int = 240) -> str:
    """One-line, truncated error — never a raw multi-line traceback to a user."""
    msg = f"{type(exc).__name__}: {exc}".replace("\n", " ").strip()
    return msg if len(msg) <= limit else msg[: limit - 1] + "…"


# --- individual checks: each returns (status, detail) -----------------------
# status "pass"/"fail" for required wheels; "skip" is allowed for soft ones
# (no GPU, no audio device on a VM) and is not a failure.


def check_torch() -> tuple[str, str]:
    import torch

    t = torch.tensor([1.0, 2.0, 3.0])
    s = float(t.sum())
    assert s == 6.0, f"tensor sum wrong: {s}"
    return "pass", f"torch {torch.__version__} (cuda={torch.cuda.is_available()}), sum ok"


def check_ctranslate2() -> tuple[str, str]:
    import ctranslate2

    n = ctranslate2.get_cuda_device_count()  # loads the native lib
    return "pass", f"ctranslate2 {ctranslate2.__version__}, cuda_devices={n}"


def check_faster_whisper() -> tuple[str, str]:
    import faster_whisper

    ver = getattr(faster_whisper, "__version__", "?")
    return "pass", f"faster_whisper {ver} import ok (model load exercised in W3)"


def check_onnxruntime() -> tuple[str, str]:
    import onnxruntime as ort

    providers = ort.get_available_providers()
    assert "CPUExecutionProvider" in providers, f"no CPU provider: {providers}"
    return "pass", f"onnxruntime {ort.__version__}, providers={providers}"


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


def check_kokoro_onnx() -> tuple[str, str]:
    import kokoro_onnx  # noqa: F401

    ver = getattr(kokoro_onnx, "__version__", "?")
    return "pass", f"kokoro_onnx {ver} import ok (assets load exercised in W3)"


def check_sherpa_onnx() -> tuple[str, str]:
    import sherpa_onnx

    ver = getattr(sherpa_onnx, "__version__", "?")
    return "pass", f"sherpa_onnx {ver} import ok"


def check_openwakeword() -> tuple[str, str]:
    import openwakeword  # noqa: F401

    return "pass", "openwakeword import ok"


def check_silero_vad() -> tuple[str, str]:
    import silero_vad  # noqa: F401

    return "pass", "silero_vad import ok"


def check_backend_imports() -> tuple[str, str]:
    # The FastAPI/OpenAI/DB spine the backend boots on.
    import aiosqlite  # noqa: F401
    import fastapi  # noqa: F401
    import openai  # noqa: F401
    import uvicorn  # noqa: F401
    import yaml  # noqa: F401

    return "pass", "fastapi/uvicorn/openai/aiosqlite/pyyaml import ok"


def check_pynvml() -> tuple[str, str]:
    # SOFT: a machine with no NVIDIA GPU is a valid cloud-only install.
    import pynvml

    try:
        pynvml.nvmlInit()
    except Exception as exc:  # noqa: BLE001
        return "skip", f"no NVIDIA GPU / NVML init failed ({_short(exc)}) — cloud-only ok"
    try:
        count = pynvml.nvmlDeviceGetCount()
        if count == 0:
            return "skip", "NVML up but 0 devices — cloud-only ok"
        h = pynvml.nvmlDeviceGetHandleByIndex(0)
        name = pynvml.nvmlDeviceGetName(h)
        if isinstance(name, bytes):
            name = name.decode()
        mem = pynvml.nvmlDeviceGetMemoryInfo(h)
        gb = mem.total / (1024**3)
        return "pass", f"GPU0 {name}, {gb:.1f} GB VRAM (8GB gate decided in W2)"
    finally:
        pynvml.nvmlShutdown()


def check_sounddevice() -> tuple[str, str]:
    # SOFT: a headless VM may have no audio device; voice still installs.
    import sounddevice as sd

    try:
        devices = sd.query_devices()
    except Exception as exc:  # noqa: BLE001
        return "skip", f"no audio device ({_short(exc)}) — mic setup deferred"
    n = len(devices) if devices is not None else 0
    return "pass", f"sounddevice ok, {n} audio devices"


def check_playwright_import() -> tuple[str, str]:
    import playwright  # noqa: F401

    ver = getattr(playwright, "__version__", "?")
    return "pass", f"playwright {ver} import ok (chromium launch: run with --browser)"


def check_playwright_launch() -> tuple[str, str]:
    # SOFT/optional: needs `playwright install chromium` first. Real op = launch
    # headless, render, read a title back.
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        try:
            browser = p.chromium.launch(headless=True)
        except Exception as exc:  # noqa: BLE001
            hint = "run: playwright install chromium"
            return "skip", f"chromium not launchable ({_short(exc)}); {hint}"
        try:
            page = browser.new_page()
            page.set_content("<title>baby-probe</title><h1>ok</h1>")
            title = page.title()
            assert title == "baby-probe", f"unexpected title {title!r}"
            return "pass", "chromium launched + rendered + read title back"
        finally:
            browser.close()


# name, required, fn
_CHECKS = [
    ("backend-imports", True, check_backend_imports),
    ("torch", True, check_torch),
    ("ctranslate2", True, check_ctranslate2),
    ("faster-whisper", True, check_faster_whisper),
    ("onnxruntime", True, check_onnxruntime),
    ("sqlite-vec", True, check_sqlite_vec),
    ("kokoro-onnx", True, check_kokoro_onnx),
    ("sherpa-onnx", True, check_sherpa_onnx),
    ("openwakeword", True, check_openwakeword),
    ("silero-vad", True, check_silero_vad),
    ("pynvml", False, check_pynvml),
    ("sounddevice", False, check_sounddevice),
    ("playwright-import", True, check_playwright_import),
]


def run(with_browser: bool) -> list[Result]:
    checks = list(_CHECKS)
    if with_browser:
        checks.append(("playwright-chromium", False, check_playwright_launch))

    results: list[Result] = []
    for name, required, fn in checks:
        try:
            status, detail = fn()
        except Exception as exc:  # noqa: BLE001 — the probe must never itself crash
            status, detail = "fail", _short(exc)
        ok = status != "fail"
        results.append(Result(name=name, required=required, ok=ok, status=status, detail=detail))
    return results


def main() -> int:
    ap = argparse.ArgumentParser(description="v6 backend-delivery functional health probe")
    ap.add_argument("--browser", action="store_true", help="also attempt a real Chromium launch")
    ap.add_argument("--json", action="store_true", help="emit a machine-readable JSON report")
    args = ap.parse_args()

    results = run(args.browser)
    required_failed = [r for r in results if r.required and not r.ok]
    ok = not required_failed

    if args.json:
        print(
            json.dumps(
                {
                    "ok": ok,
                    "python": sys.version.split()[0],
                    "platform": platform.platform(),
                    "results": [asdict(r) for r in results],
                },
                indent=2,
            )
        )
    else:
        glyph = {"pass": "PASS", "fail": "FAIL", "skip": "skip"}
        print(f"backend-delivery functional probe  (python {sys.version.split()[0]})")
        print(f"  {platform.platform()}")
        print("-" * 72)
        for r in results:
            tag = "" if r.required else " (optional)"
            print(f"  [{glyph[r.status]}] {r.name}{tag}: {r.detail}")
        print("-" * 72)
        if ok:
            print("RESULT: all required wheels import + function.")
        else:
            print("RESULT: FAILED — required wheels not functional:")
            for r in required_failed:
                print(f"    - {r.name}: {r.detail}")
            print("  (a green `uv sync` is not enough — this is why the probe exists.)")

    return 0 if ok else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(130)
    except Exception:  # noqa: BLE001
        traceback.print_exc()
        sys.exit(2)
