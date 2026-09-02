"""v6 W3: first-run provisioning orchestrator + progress streaming.

Walks the dependency manifest for the chosen install mode, fetches the missing
pieces with real byte/%/ETA progress, then re-verifies functionally via
core.health. Every step emits a JSON progress event the wizard renders. Failures
are classified into a legible, retryable message -- never a raw trace -- and the
network steps resume from where they stopped (HTTP Range for file downloads;
Ollama's content-addressed blobs for the model pull).

Division of labor: the OS-level installs that need elevation or a package manager
(the VC++ redist, the Ollama daemon) are DETECTED and reported here, but INSTALLED
by the first-run harness (W3d) -- a per-user backend can't cleanly UAC-elevate.
This module owns the downloads (kokoro, the optional CAM++ model), the resumable
Ollama model pull (ported from the W0 spike), the HF-cache auto-download triggers
(whisper + e5), the low-disk pre-check, and the final health re-verify.

`provision()` reports through a plain `on_event(dict)` callback so it's decoupled
from the bus and unit-testable; the endpoint wraps it to publish onto the bus and
keep a per-dependency snapshot for reconnecting wizards.
"""

from __future__ import annotations

import asyncio
import json
import re
import shutil
import time
from collections.abc import Callable
from dataclasses import asdict
from pathlib import Path
from urllib.parse import unquote

from core import health, manifest, paths

OnEvent = Callable[[dict], None]

_OLLAMA = "http://127.0.0.1:11434"
_DAILY_MODEL = "qwen3.5:9b-q4_K_M"
# Headroom over the summed asset sizes for temp/extract/index churn.
_DISK_SLACK_MB = 2000


# --- byte helpers -----------------------------------------------------------


def _human(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(n) < 1024:
            return f"{n:.1f}{unit}"
        n /= 1024
    return f"{n:.1f}PB"


def _rate(done: int, total: int, elapsed_s: float, moved: int) -> dict:
    """Pure progress math for a byte stream: pct, speed, ETA. Kept separate so the
    download/pull loops stay thin and this is unit-tested directly."""
    speed = moved / elapsed_s if elapsed_s > 0 else 0.0
    pct = (100.0 * done / total) if total else 0.0
    eta = ((total - done) / speed) if (speed > 0 and total) else 0.0
    return {
        "bytes_done": done,
        "bytes_total": total,
        "pct": round(pct, 1),
        "speed_bps": round(speed),
        "eta_s": round(eta),
        "human": f"{_human(done)}/{_human(total)}" if total else _human(done),
    }


def _event(dep: str, phase: str, *, status: str, **extra) -> dict:
    """One progress event. phase: check|download|install|verify|done|error|skip."""
    return {"dep": dep, "phase": phase, "status": status, **extra}


# --- failure classifier (Python port of the W0 Resolve-SyncError) -----------

_PROXY = re.compile(r"proxy|\b407\b|proxy tunnel|CONNECT tunnel", re.I)
_NONET = re.compile(
    r"getaddrinfo|temporary failure|dns error|no such host|os error 11001|resolve|"
    r"network is unreachable|connection reset|timed out|timeout|error sending request|"
    r"connect|failed to connect|connection refused|unreachable",
    re.I,
)
_DISK = re.compile(r"no space left|disk full|enospc|not enough space", re.I)
_CORRUPT = re.compile(r"hash mismatch|checksum|corrupt|incomplete", re.I)
_STALLED = re.compile(r"made no progress", re.I)


def classify_error(text: str) -> dict:
    """Reframe a download/sync failure as a legible, actionable, retryable message.
    Order matters: an explicit proxy signal wins, then no-network, then disk, then
    corruption. A bare 'connect' token is treated as no-network, not proxy (the W0
    spike caught that misclassification)."""
    t = text or ""
    # First: a step we gave up on ourselves. It reads like nothing else here, and the
    # advice is different -- the transfer was reachable, it just stopped moving.
    if _STALLED.search(t):
        return {
            "kind": "stalled",
            "message": "That step stopped making progress, so it was given up on rather "
            "than left hanging. Retry -- it resumes from what is already downloaded. If "
            "it stalls in the same place again, suspect a proxy, a VPN, or antivirus "
            "scanning the transfer.",
            "retryable": True,
        }
    if _PROXY.search(t):
        return {
            "kind": "proxy",
            "message": "A proxy/firewall looks to be blocking the download. Set "
            "HTTPS_PROXY (and HTTP_PROXY) to your corporate proxy, then retry -- it "
            "resumes from where it stopped.",
            "retryable": True,
        }
    if _NONET.search(t):
        return {
            "kind": "no_network",
            "message": "Couldn't reach the download server. Reconnect and retry -- "
            "nothing already downloaded is lost.",
            "retryable": True,
        }
    if _DISK.search(t):
        return {
            "kind": "disk_full",
            "message": "Ran out of disk space. Free a few GB and retry.",
            "retryable": True,
        }
    if _CORRUPT.search(t):
        return {
            "kind": "corrupt",
            "message": "A download was corrupted (partial/interrupted transfer). "
            "Retry -- only the bad file is re-fetched.",
            "retryable": True,
        }
    return {
        "kind": "unknown",
        "message": "Setup step failed. Retry once connected; if it persists, copy "
        "the details into an issue. Downloads are safe to re-run -- they resume.",
        "retryable": True,
    }


# --- low-disk pre-check ------------------------------------------------------


def check_disk(
    mode: str, path: str | Path | None = None, *, include_optional: bool = False
) -> dict:
    """Free space vs the manifest's footprint for `mode`, before the multi-GB pulls."""
    root = Path(path) if path else paths.baby_home()
    # disk_usage needs an existing dir; walk up to the first that exists.
    probe = root
    while not probe.exists() and probe != probe.parent:
        probe = probe.parent
    free_mb = shutil.disk_usage(str(probe)).free // (1024 * 1024)
    need_mb = manifest.disk_footprint_mb(mode, include_optional=include_optional) + _DISK_SLACK_MB
    return {"ok": free_mb >= need_mb, "free_mb": int(free_mb), "need_mb": int(need_mb)}


# --- primitives: file download (resumable) + ollama pull (resumable) ---------


def _dest_name(url: str) -> str:
    """Local filename for a download; decode %2B etc. (the CAM++ url is encoded)."""
    return unquote(url.rsplit("/", 1)[-1])


async def _stream_to(
    part: Path, r, fmode: str, pos: int, total: int, on_event: OnEvent, dep: str
) -> None:
    """Write a response body to `part`, emitting throttled byte progress."""
    done = pos
    t0 = time.monotonic()
    last = 0.0
    with open(part, fmode) as f:
        async for chunk in r.aiter_bytes(1 << 20):
            f.write(chunk)
            done += len(chunk)
            now = time.monotonic()
            if now - last >= 0.5:
                on_event(_event(dep, "download", status="working",
                                **_rate(done, total, now - t0, done - pos)))
                last = now


async def download_file(
    url: str,
    dest: Path,
    *,
    on_event: OnEvent,
    dep: str,
    retries: int = 3,
) -> None:
    """Stream `url` to `dest`, resuming a partial `.part` via HTTP Range. Emits
    download progress; classifies + re-raises on final failure."""
    import httpx

    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_suffix(dest.suffix + ".part")
    timeout = httpx.Timeout(60.0, connect=30.0)

    for attempt in range(1, retries + 1):
        pos = part.stat().st_size if part.exists() else 0
        headers = {"Range": f"bytes={pos}-"} if pos else {}
        try:
            async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
                async with client.stream("GET", url, headers=headers) as r:
                    # 416 to a Range request means the .part already covers the whole
                    # resource (a completed download whose promote was interrupted).
                    # Promote it instead of dead-ending on repeated 416s forever.
                    if r.status_code == 416 and pos > 0:
                        pass
                    elif r.status_code not in (200, 206):
                        raise RuntimeError(f"HTTP {r.status_code} for {url}")
                    elif r.status_code == 200:
                        # Server ignored our Range -> restart the file from scratch.
                        pos = 0
                        total = int(r.headers.get("content-length", 0) or 0)
                        await _stream_to(part, r, "wb", pos, total, on_event, dep)
                    else:  # 206 partial -> append and resume.
                        total = pos + int(r.headers.get("content-length", 0) or 0)
                        await _stream_to(part, r, "ab", pos, total, on_event, dep)
            part.replace(dest)
            on_event(_event(dep, "download", status="done", detail=f"{dest.name} ready"))
            return
        except Exception as exc:  # noqa: BLE001 -- classified + retried/re-raised
            cls = classify_error(str(exc))
            if attempt >= retries:
                on_event(_event(dep, "error", status="error", **cls))
                raise
            await asyncio.sleep(min(30, 2**attempt))


def _pull_progress(msg: dict, starts: dict, now: float) -> dict | None:
    """Turn one /api/pull JSON line into a progress event dict, or None for the
    non-byte phases (pulling manifest / verifying / writing). Pure -- unit-tested."""
    total = msg.get("total")
    done = msg.get("completed")
    digest = msg.get("digest", "")
    if total and done is not None and digest:
        t0, b0 = starts.setdefault(digest, (now, done))
        return _event("ollama-model", "download", status="working",
                      **_rate(done, total, now - t0, done - b0))
    return None


async def pull_ollama_model(
    model: str, *, on_event: OnEvent, host: str = _OLLAMA, retries: int = 3
) -> None:
    """Stream a resumable Ollama pull (POST /api/pull), rendering live byte progress.
    Ported from the W0 spike; resumable by construction (content-addressed blobs)."""
    import httpx

    for attempt in range(1, retries + 1):
        starts: dict = {}
        try:
            # Unbounded overall (the pull is multi-GB), but a mid-stream STALL must
            # raise ReadTimeout so it's classified + retried, not hung forever.
            pull_timeout = httpx.Timeout(None, connect=30.0, read=120.0)
            async with httpx.AsyncClient(timeout=pull_timeout) as client:
                async with client.stream("POST", f"{host}/api/pull",
                                         json={"model": model, "stream": True}) as r:
                    if r.status_code != 200:
                        raise RuntimeError(f"/api/pull returned {r.status_code}")
                    async for line in r.aiter_lines():
                        if not line:
                            continue
                        try:
                            msg = json.loads(line)
                        except json.JSONDecodeError:
                            continue
                        if "error" in msg:
                            raise RuntimeError(msg["error"])
                        ev = _pull_progress(msg, starts, time.monotonic())
                        if ev is not None:
                            on_event(ev)
                        elif msg.get("status"):
                            on_event(_event("ollama-model", "download", status="working",
                                            detail=msg["status"]))
            on_event(_event("ollama-model", "download", status="done", detail=f"{model} pulled"))
            return
        except Exception as exc:  # noqa: BLE001
            cls = classify_error(str(exc))
            if attempt >= retries:
                on_event(_event("ollama-model", "error", status="error", **cls))
                raise
            await asyncio.sleep(min(30, 2**attempt))


async def _ollama_healthy(host: str = _OLLAMA) -> bool:
    import httpx

    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            r = await client.get(f"{host}/api/tags")
            return r.status_code == 200
    except Exception:  # noqa: BLE001
        return False


# Blocking loaders run in a worker thread -- their only job is to TRIGGER the HF-hub
# auto-download (whisper, e5); the final health re-verify proves they function.
def _download_whisper() -> None:
    from voice.stt import SpeechToText

    SpeechToText().load()


def _download_embedder() -> None:
    from memory.embedder import Embedder

    asyncio.run(Embedder().warmup())


def _download_openwakeword(target: Path) -> None:
    """Fetch openWakeWord's 17 files into `target` rather than its own site-packages
    dir -- the default lands them in the venv, where the next `uv sync` deletes them
    (see core.paths.wakeword_dir). Skips any file already there."""
    import openwakeword.utils

    target.mkdir(parents=True, exist_ok=True)
    openwakeword.utils.download_models(target_directory=str(target))


# --- hub steps: progress for a loader that reports none ---------------------
# The hub gives no byte callback, so these steps used to emit ONE "working" event and
# then nothing for the whole multi-GB fetch. That made a healthy slow download, a
# wedged one, and a backend that had died outright render identically -- a first run
# sat on "Memory embedder ... working" for three hours after the process was already
# gone. The cache directory is the one honest progress signal available, so poll it.

_HB_S = 15  # heartbeat period
_STALL_S = 600  # no new bytes this long -> say so in the row
_HUB_TIMEOUT_S = 3600  # hard ceiling: a step must resolve one way or the other


def _hub_asset(dep_key: str) -> tuple[str, int]:
    """(hf repo id, approx MB) for a dep whose asset is a hub repo. Derived from the
    manifest so sizes/urls stay single-sourced."""
    asset = manifest.get(dep_key).assets[0]
    return asset.url.split("huggingface.co/", 1)[-1].strip("/"), asset.approx_mb


def _hub_cache_dir(repo: str) -> Path | None:
    """Where huggingface_hub stores `repo`. None when the constant can't be read --
    progress then degrades to elapsed-time only, never to an error."""
    try:
        from huggingface_hub.constants import HF_HUB_CACHE
    except Exception:  # noqa: BLE001 -- progress reporting must never fail a download
        return None
    return Path(HF_HUB_CACHE) / ("models--" + repo.replace("/", "--"))


def _dir_bytes(path: Path | None) -> int:
    if path is None:
        return 0
    total = 0
    try:
        for p in path.rglob("*"):
            try:
                if p.is_file():
                    total += p.stat().st_size
            except OSError:  # vanished mid-walk (hub renames .incomplete blobs)
                continue
    except OSError:
        return 0
    return total


def _hub_detail(seen: int, size_mb: int, elapsed_s: float, stalled_s: float) -> str:
    """The row's text. Pure, so the wording is unit-tested without a download."""
    got = seen / (1024 * 1024)
    mins = int(elapsed_s // 60)
    # Past the download the loader builds the model in memory and the cache stops
    # growing. That is not a stall, so don't cry wolf at the one moment it's normal.
    if size_mb and got >= size_mb * 0.95:
        return f"loading the model ({mins}m)"
    where = f"{got:.0f} of ~{size_mb} MB"
    if stalled_s >= _STALL_S:
        return f"{where} -- no new data for {int(stalled_s // 60)}m"
    return f"downloading {where} ({mins}m)"


async def _run_hub_step(dep: str, loader: Callable[[], None], *, on_event: OnEvent) -> None:
    """Run a blocking hub loader, reporting progress measured from the cache dir.

    The thread cannot be cancelled, so the timeout does not kill the download -- it
    ends our claim to be working and lets the wizard show a real, retryable error
    instead of a spinner with no end condition.
    """
    repo, size_mb = _hub_asset(dep)
    cache = _hub_cache_dir(repo)
    started = time.monotonic()
    seen = last_seen = _dir_bytes(cache)
    moved_at = started

    on_event(_event(dep, "download", status="working",
                    detail=_hub_detail(seen, size_mb, 0.0, 0.0), bytes=seen))

    task = asyncio.ensure_future(asyncio.to_thread(loader))
    try:
        while True:
            done, _ = await asyncio.wait({task}, timeout=_HB_S)
            if done:
                break
            now = time.monotonic()
            seen = _dir_bytes(cache)
            if seen > last_seen:
                last_seen, moved_at = seen, now
            elapsed = now - started
            if elapsed > _HUB_TIMEOUT_S:
                raise TimeoutError(
                    f"{manifest.get(dep).label} made no progress for "
                    f"{int(elapsed // 60)} minutes -- giving up so this can be retried"
                )
            on_event(_event(dep, "download", status="working", bytes=seen,
                            elapsed_s=int(elapsed),
                            detail=_hub_detail(seen, size_mb, elapsed, now - moved_at)))
        await task  # re-raise whatever the loader raised
    except Exception as exc:  # noqa: BLE001 -- classify, never a raw HF/httpx trace
        # A running executor thread ignores cancel(), so the loader may still finish
        # (or raise) long after we stopped waiting. Swallow that result rather than
        # letting asyncio log a bare "exception was never retrieved" into the log we
        # just made people rely on. A retry starts a fresh loader; the hub's own file
        # locks keep the two from corrupting the cache.
        task.cancel()
        task.add_done_callback(lambda t: None if t.cancelled() else t.exception())
        on_event(_event(dep, "error", status="error", **classify_error(str(exc))))
        raise
    on_event(_event(dep, "download", status="done", detail="ready"))


# --- the plan (what the wizard checklist shows, in walk order) --------------


def plan(mode: str) -> list[dict]:
    """The ordered steps provision() will emit, for the wizard's upfront checklist.
    Mirrors the walk exactly so no live event lands on a row the checklist lacks.
    Labels/sizes come from the manifest (single source)."""

    def step(key: str, label: str, *, required: bool = True, size_mb: int = 0) -> dict:
        return {"key": key, "label": label, "required": required, "size_mb": size_mb}

    def mb(dep_key: str) -> int:
        return sum(a.approx_mb for a in manifest.get(dep_key).assets)

    steps = [
        step("disk", "Checking disk space"),
        step("vcredist", manifest.get("vcredist").label),
        step("kokoro", manifest.get("kokoro").label, size_mb=mb("kokoro")),
        step("wakeword", manifest.get("wakeword").label, size_mb=mb("wakeword")),
        step("whisper", manifest.get("whisper").label, size_mb=mb("whisper")),
        step("embedder", manifest.get("embedder").label, size_mb=mb("embedder")),
        step("speaker", manifest.get("speaker").label, required=False, size_mb=mb("speaker")),
    ]
    if mode == "full":
        steps.append(step("ollama-daemon", manifest.get("ollama-daemon").label))
        steps.append(
            step("ollama-model", manifest.get("ollama-model").label, size_mb=mb("ollama-model"))
        )
    steps.append(step("verify", "Verifying everything works"))
    return steps


# --- the orchestrator -------------------------------------------------------


async def provision(mode: str, *, on_event: OnEvent, browser: bool = False) -> dict:
    """Fetch everything `mode` needs, then re-verify. Returns {ok, report}.

    Ordering: disk pre-check -> VC++ detect (install deferred) -> explicit model
    downloads (kokoro; CAM++ optional) -> openWakeWord assets -> whisper + e5 HF
    triggers -> [Full] Ollama daemon check + 9B pull -> functional re-verify.
    """
    md = paths.models_dir()

    disk = check_disk(mode)
    on_event(_event("disk", "check", status="pass" if disk["ok"] else "error", **disk))
    if not disk["ok"]:
        return {"ok": False, "reason": "low_disk", "disk": disk}

    vc_status, vc_detail = health.check_vcredist()
    on_event(_event("vcredist", "check",
                    status="pass" if vc_status == "pass" else "needs_install",
                    detail=vc_detail))

    # Required explicit downloads: kokoro model + voices.
    for asset in manifest.get("kokoro").assets:
        dest = md / _dest_name(asset.url)
        await _fetch_if_absent(asset.url, dest, on_event=on_event, dep="kokoro")

    # openWakeWord assets (into models_dir, NOT the venv; skips any already present).
    on_event(_event("wakeword", "download", status="working", detail="fetching wake-word models"))
    await asyncio.to_thread(_download_openwakeword, paths.wakeword_dir())
    on_event(_event("wakeword", "download", status="done", detail="wake-word models ready"))

    # HF-cache auto-downloads. The hub reports no bytes of its own, so _run_hub_step
    # measures the cache dir and enforces a ceiling -- see its docstring.
    for dep, loader in (("whisper", _download_whisper), ("embedder", _download_embedder)):
        await _run_hub_step(dep, loader, on_event=on_event)

    # Optional CAM++ speaker model -- best-effort; never fail the run.
    for asset in manifest.get("speaker").assets:
        dest = md / _dest_name(asset.url)
        try:
            await _fetch_if_absent(asset.url, dest, on_event=on_event, dep="speaker")
        except Exception:  # noqa: BLE001 -- optional; verify stays off until enrollment
            on_event(_event("speaker", "skip", status="skip", detail="optional -- skipped"))

    # Full mode: the local 9B. Daemon install is the harness's job (W3d); here we
    # pull only if it's already reachable, else report it as an installer step.
    if mode == "full":
        if await _ollama_healthy():
            # Answer the row either way. This branch used to emit nothing at all, so
            # an Ollama that was ALREADY running left "Ollama runtime" grey for the
            # whole session -- a finished install rendering as one still in progress,
            # right next to the 9B it had just pulled through that same daemon.
            on_event(_event("ollama-daemon", "check", status="pass",
                            detail="Ollama is running"))
            await pull_ollama_model(_DAILY_MODEL, on_event=on_event)
        else:
            on_event(_event("ollama-daemon", "check", status="needs_install",
                            detail="Ollama isn't running yet -- the installer sets it up"))

    # Functional re-verify: does everything actually work?
    results = await asyncio.to_thread(health.run_all, mode, "full", browser)
    ok = health.overall_ok(results)
    on_event(_event("verify", "verify", status="pass" if ok else "fail",
                    detail=health.readiness_summary(results),
                    results=[asdict(r) for r in results]))
    if ok:
        paths.write_setup({"provisioned": True})
    return {"ok": ok, "report": health.readiness_summary(results)}


async def _fetch_if_absent(url: str, dest: Path, *, on_event: OnEvent, dep: str) -> None:
    if dest.exists() and dest.stat().st_size > 0:
        on_event(_event(dep, "skip", status="present", detail=f"{dest.name} already present"))
        return
    await download_file(url, dest, on_event=on_event, dep=dep)
