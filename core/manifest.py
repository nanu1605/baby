"""v6 W3: the first-run dependency manifest -- the single source of truth for what
a stranger's clean Windows PC must acquire before Baby works.

This is *declarative data*, verified against the real source + `scripts/setup.ps1`
(the dev provisioner) in W3's understand pass. The orchestrator (W3b) walks it to
download/install, the wizard checklist (W3c) renders it, the first-run harness
(W3d) drives it, and the health check (W3e, `core/health.py`) verifies each entry
*functionally*. Nothing here imports a heavy dep -- it's pure metadata.

Two facts every entry pins down, because getting them wrong ships a broken install:

* **auto_downloads** -- does the library fetch the asset itself on first load (the HF
  hub cache for whisper/e5; the wheel for espeak-ng/sqlite-vec), or must first-run
  fetch it explicitly (kokoro, the 9B, openWakeWord, Chromium)? An explicit asset
  that first-run skips = a hard crash or silent dead feature later.
* **dest** -- where it lands. Anything the app loads *by path* (kokoro, CAM++) must
  live under `%LOCALAPPDATA%\\baby\\models` (per-user writable), NOT the read-mostly
  install dir -- see `core.paths.models_dir`. HF-cache / venv-site-packages assets
  are already per-user writable, so they only need triggering, not relocation.
"""

from __future__ import annotations

from dataclasses import dataclass

# How first-run acquires a dependency -- tells the orchestrator which mechanism to use.
#   uv_sync        : comes in with `uv sync` (wheels); no separate fetch
#   managed_python : `uv python install` (Astral python-build-standalone)
#   hf_cache       : auto-downloads to the per-user HuggingFace hub cache on first load
#   explicit_url   : first-run must download the file(s) to models_dir()
#   download_models: openWakeWord's own downloader, into venv site-packages
#   ollama_pull    : POST /api/pull streaming (resumable), Full mode only
#   winget         : silent winget/installer (Ollama daemon, LHM)
#   vc_redist      : Microsoft redist installer -- needs admin/elevation
#   playwright     : `playwright install chromium`
INSTALL_KINDS = frozenset(
    {
        "uv_sync",
        "managed_python",
        "hf_cache",
        "explicit_url",
        "download_models",
        "ollama_pull",
        "winget",
        "vc_redist",
        "playwright",
    }
)


@dataclass(frozen=True)
class Asset:
    """One downloadable/installed artifact of a dependency."""

    name: str
    approx_mb: int
    auto_downloads: bool  # True: the lib/uv fetches it; False: first-run must fetch it
    dest: str = ""  # venv | hf_cache | models_dir | ollama | ms-playwright | system | bundled
    url: str = ""


@dataclass(frozen=True)
class Dep:
    """A first-run dependency. `mode_gated="full"` means it's only needed for a Full
    (local+cloud) install; a cloud-only install skips it entirely. `probe` names the
    functional check in core/health.py that verifies it actually works."""

    key: str
    label: str
    required: bool
    install_kind: str
    category: str
    probe: str = ""
    mode_gated: str | None = None  # None (both modes) | "full"
    assets: tuple[Asset, ...] = ()
    needs_admin: bool = False
    silent: bool = True
    note: str = ""


# --- the manifest -----------------------------------------------------------
# Order = a sensible install order (runtime + wheels first, then models, then the
# heavy Full-only 9B, then optional extras).

MANIFEST: tuple[Dep, ...] = (
    Dep(
        key="python-backend",
        label="Python runtime + libraries",
        required=True,
        install_kind="uv_sync",
        category="runtime",
        probe="wheels",
        note="Bundled uv.exe -> `uv python install 3.13` -> `uv sync --frozen` into "
        "%LOCALAPPDATA%\\baby\\.venv (UV_PROJECT_ENVIRONMENT). Brings every native "
        "wheel (torch, ctranslate2, onnxruntime, sqlite-vec, kokoro-onnx, sherpa-onnx, "
        "openwakeword, silero-vad, espeakng-loader) -- all verified by the 'wheels' probe.",
        assets=(
            Asset("managed CPython 3.13", 40, auto_downloads=True, dest="venv",
                  url="Astral python-build-standalone (uv python install)"),
            Asset("PyPI wheel set (torch dominates)", 1300, auto_downloads=True, dest="venv",
                  url="https://pypi.org/simple"),
        ),
    ),
    Dep(
        key="vcredist",
        label="Visual C++ runtime",
        required=True,
        install_kind="vc_redist",
        category="runtime",
        probe="vcredist",
        needs_admin=True,
        note="MSVC 2015-2022 x64 runtime (vcruntime140.dll / vcruntime140_1.dll / "
        "msvcp140.dll). Every native wheel dlopens it; a clean image may lack it or "
        "carry an older one missing vcruntime140_1.dll. Detect via System32 DLLs + "
        "registry Bld>=30704, then silent-install. NEEDS ADMIN -- collides with the "
        "no-admin per-user model; first-run must UAC-elevate for just this step.",
        assets=(
            Asset("vc_redist.x64.exe", 25, auto_downloads=False, dest="system",
                  url="https://aka.ms/vs/17/release/vc_redist.x64.exe"),
        ),
    ),
    Dep(
        key="whisper",
        label="Speech-to-text model (Whisper large-v3-turbo)",
        required=True,
        install_kind="hf_cache",
        category="voice",
        probe="whisper",
        note="faster-whisper auto-downloads to the per-user HF hub cache on first "
        "construct -- location is fine, but first-run should pre-fetch with progress "
        "so it doesn't stall mid-conversation on first voice use.",
        assets=(
            Asset("faster-whisper-large-v3-turbo (CT2)", 1600, auto_downloads=True, dest="hf_cache",
                  url="huggingface.co/mobiuslabsgmbh/faster-whisper-large-v3-turbo"),
        ),
    ),
    Dep(
        key="embedder",
        label="Memory embedder (e5-small)",
        required=True,
        install_kind="hf_cache",
        category="memory",
        probe="embedder",
        note="sentence-transformers auto-downloads intfloat/multilingual-e5-small to "
        "the HF cache on first warmup. Skipped pre-fetch => memory RAG is silently OFF "
        "on an offline first boot. Output dim MUST be 384 (matches the vec0 table).",
        assets=(
            Asset("intfloat/multilingual-e5-small", 471, auto_downloads=True, dest="hf_cache",
                  url="huggingface.co/intfloat/multilingual-e5-small"),
        ),
    ),
    Dep(
        key="kokoro",
        label="Text-to-speech model (Kokoro)",
        required=True,
        install_kind="explicit_url",
        category="voice",
        probe="kokoro",
        note="Kokoro does NOT auto-download (unlike whisper). First-run MUST fetch the "
        "onnx + voices into models_dir() or the first spoken reply hard-crashes with "
        "FileNotFoundError. Third-party GitHub release -- a deleted release breaks it.",
        assets=(
            Asset("kokoro-v1.0.onnx", 310, auto_downloads=False, dest="models_dir",
                  url="https://github.com/thewh1teagle/kokoro-onnx/releases/download/"
                      "model-files-v1.0/kokoro-v1.0.onnx"),
            Asset("voices-v1.0.bin", 27, auto_downloads=False, dest="models_dir",
                  url="https://github.com/thewh1teagle/kokoro-onnx/releases/download/"
                      "model-files-v1.0/voices-v1.0.bin"),
        ),
    ),
    Dep(
        key="wakeword",
        label="Wake-word models (openWakeWord)",
        required=True,
        install_kind="download_models",
        category="voice",
        probe="wakeword",
        note="openWakeWord ships NO model files in its wheel; `uv sync` leaves "
        "resources/models empty and Model() raises. First-run must run "
        "openwakeword.utils.download_models() (into the per-user venv site-packages). "
        "hey_jarvis is the OOTB fallback; the custom jarvis.onnx is optional/absent.",
        assets=(
            Asset("feature + VAD + wake models (17 files)", 19, auto_downloads=False, dest="venv",
                  url="github.com/dscripka/openWakeWord/releases/download/v0.5.1"),
        ),
    ),
    Dep(
        key="ollama-daemon",
        label="Ollama runtime (local brain host)",
        required=True,
        install_kind="winget",
        category="llm",
        probe="ollama",
        mode_gated="full",
        note="Full mode only. Silent-install via winget Ollama.Ollama (fallback: "
        "OllamaSetup.exe /VERYSILENT), then serve + poll GET /api/tags. Set "
        "OLLAMA_CONTEXT_LENGTH=8192 or served context silently truncates.",
        assets=(
            Asset("Ollama Windows runtime", 700, auto_downloads=False, dest="system",
                  url="winget Ollama.Ollama / https://ollama.com/download/OllamaSetup.exe"),
        ),
    ),
    Dep(
        key="ollama-model",
        label="Local 9B brain (qwen3.5:9b-q4_K_M)",
        required=True,
        install_kind="ollama_pull",
        category="llm",
        probe="ollama-model",
        mode_gated="full",
        note="Full mode only. NOT auto-pulled -- /v1 chat errors on a missing model, "
        "only `ollama run` auto-pulls. First-run POSTs /api/pull with stream=true "
        "(bytes/%/ETA, resumable via content-addressed blobs). ~6.6 GB -- the scariest "
        "first-run moment; must survive a dropped connection.",
        assets=(
            Asset("qwen3.5:9b-q4_K_M weights", 6600, auto_downloads=False, dest="ollama",
                  url="Ollama registry (/api/pull)"),
        ),
    ),
    Dep(
        key="speaker",
        label="Speaker verification model (CAM++)",
        required=False,
        install_kind="explicit_url",
        category="voice",
        probe="speaker",
        note="Optional/fail-soft: speaker verify is OFF until the owner enrolls, so a "
        "stranger never needs it OOTB. If fetched, lands in models_dir(). The '+' must "
        "be %2B-encoded in the URL. Exclude the 3 bench-only speaker models (~143 MB).",
        assets=(
            Asset("wespeaker_en_voxceleb_CAM++.onnx", 28, auto_downloads=False, dest="models_dir",
                  url="https://github.com/k2-fsa/sherpa-onnx/releases/download/"
                      "speaker-recongition-models/wespeaker_en_voxceleb_CAM%2B%2B.onnx"),
        ),
    ),
    Dep(
        key="chromium",
        label="Browser automation (Playwright Chromium)",
        required=False,
        install_kind="playwright",
        category="tools",
        probe="chromium",
        note="Optional: the browser_act tool. The pip wheel ships only the driver -- "
        "`playwright install chromium` fetches the ~170 MB browser to the ms-playwright "
        "cache. Skipped => browser_act fails on first use (caught, returned as an "
        "error), every other feature works.",
        assets=(
            Asset("Chromium build", 170, auto_downloads=False, dest="ms-playwright",
                  url="playwright install chromium"),
        ),
    ),
    Dep(
        key="everything",
        label="File search (Everything)",
        required=False,
        install_kind="winget",
        category="tools",
        probe="",  # no functional probe -- file_search fails soft to an os.scandir walk
        note="Optional: the file_search tool uses voidtools Everything for instant "
        "indexed search and degrades silently to a slower os.scandir walk when absent "
        "(tools/_everything.py). winget installs the app; the Everything64.dll SDK is "
        "what the Python binding needs. No health probe -- the fallback is transparent.",
        assets=(
            Asset("Everything + Everything64.dll SDK", 5, auto_downloads=False, dest="system",
                  url="winget voidtools.Everything"),
        ),
    ),
    Dep(
        key="sensors",
        label="Hardware sensors (LibreHardwareMonitor)",
        required=False,
        install_kind="winget",
        category="tools",
        probe="sensors",
        silent=False,
        note="Optional: CPU/GPU temps for get_sensors. winget installs the app, but the "
        "web server + admin elevation are MANUAL GUI steps that can't be scripted -- so "
        "this can't fully auto-provision. get_sensors fails soft (structured error) when "
        "absent; never fail install on it.",
        assets=(
            Asset("LibreHardwareMonitor", 2, auto_downloads=False, dest="system",
                  url="winget LibreHardwareMonitor.LibreHardwareMonitor"),
        ),
    ),
)


# --- queries the orchestrator / wizard / disk pre-check use ------------------


def deps_for_mode(mode: str, *, include_optional: bool = True) -> tuple[Dep, ...]:
    """The deps that apply to an install `mode` ("full" | "cloud_only"). Drops
    Full-only entries (Ollama + 9B) for a cloud-only install, and optional deps
    when `include_optional` is False."""
    out = []
    for d in MANIFEST:
        if d.mode_gated is not None and d.mode_gated != mode:
            continue
        if not include_optional and not d.required:
            continue
        out.append(d)
    return tuple(out)


def required_deps(mode: str) -> tuple[Dep, ...]:
    """Deps that MUST pass for first-run to complete in this mode."""
    return deps_for_mode(mode, include_optional=False)


def disk_footprint_mb(mode: str, *, include_optional: bool = False) -> int:
    """Approx MB that lands on disk for `mode` -- the low-disk pre-check budget
    before the multi-GB pulls. Sums every asset (auto or explicit) since they all
    consume disk on the target."""
    return sum(
        a.approx_mb
        for d in deps_for_mode(mode, include_optional=include_optional)
        for a in d.assets
    )


def explicit_fetches(mode: str, *, include_optional: bool = True) -> tuple[Dep, ...]:
    """Deps first-run must ACTIVELY fetch/install (auto_downloads is False for at
    least one asset, or the install_kind is an active step) -- i.e. everything that
    won't just happen for free during `uv sync` / lazy load. Drives the progress
    checklist so nothing silently no-ops."""
    active = {"explicit_url", "download_models", "ollama_pull", "winget", "vc_redist", "playwright"}
    return tuple(d for d in deps_for_mode(mode, include_optional=include_optional)
                 if d.install_kind in active)


def get(key: str) -> Dep:
    """Look up a dep by key; raises KeyError if unknown."""
    for d in MANIFEST:
        if d.key == key:
            return d
    raise KeyError(key)


# Keys the orchestrator treats as pre-fetch-recommended-but-lazy-safe (auto-download
# on first use if skipped) vs must-fetch-or-broken. Handy for messaging.
LAZY_SAFE_KEYS: frozenset[str] = frozenset({"whisper", "embedder"})
