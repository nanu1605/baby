r"""A first run must never sit on "working" with no end condition.

An installed build downloaded its models, then the backend process died. Nothing
noticed: the shell probes :8765 once at startup and never again, the hub steps emitted
a single "working" event for a multi-GB fetch and nothing after it, and the log the
crash should have landed in did not exist because run.py created it AFTER the imports
that can fail. The wizard showed "Memory embedder (e5-small) - working" for three
hours against a process that had already exited, and left no trace to diagnose.

Three guards, one per hole:

  * The hub steps report progress measured off the cache directory, distinguish a
    stall from the (normal) no-growth model-load phase, and give up at a ceiling so a
    row always resolves to done or a retryable error.
  * run.py opens its log BEFORE importing anything of Baby's, so an import failure
    inside a windowed process is recorded rather than vanishing.
  * The shell watches the child it spawned and says so when it exits on its own.

The last two are source-order/wiring guards rather than simulations: neither a
pythonw crash nor a Tauri window can be produced in a unit test, but both regressions
are re-introduced by an edit that moves a line, which is exactly what these catch.
"""

from __future__ import annotations

import asyncio
import re
from pathlib import Path

import pytest

from core import manifest, provision

_ROOT = Path(__file__).resolve().parent.parent


# --- hub step progress ------------------------------------------------------


def test_stall_is_its_own_error_kind():
    """A transfer that stops moving is not a network failure -- the server was
    reachable. It gets its own advice (proxy/VPN/AV), and stays retryable."""
    cls = provision.classify_error(
        "Memory embedder (e5-small) made no progress for 60 minutes"
    )
    assert cls["kind"] == "stalled"
    assert cls["retryable"] is True
    assert "resumes" in cls["message"]


def test_detail_reports_bytes_not_a_fixed_string():
    """The old row said "downloading (~471 MB)" for the whole fetch, so a healthy
    download and a dead backend looked identical. It must carry live numbers."""
    detail = provision._hub_detail(100 * 1024 * 1024, 471, 90.0, 0.0)
    assert "100" in detail and "471" in detail


def test_detail_names_a_stall_once_nothing_has_moved():
    quiet = provision._hub_detail(100 * 1024 * 1024, 471, 1200.0, provision._STALL_S + 60)
    assert "no new data" in quiet


def test_a_finished_download_reads_as_loading_not_stalled():
    """The cache stops growing while the loader builds the model in memory. Calling
    that a stall would cry wolf at the one moment no bytes are expected."""
    detail = provision._hub_detail(470 * 1024 * 1024, 471, 300.0, 10_000.0)
    assert "loading" in detail
    assert "no new data" not in detail


def test_hub_asset_resolves_the_repo_the_cache_is_keyed_by():
    """Progress is read from %HF_HOME%/models--<org>--<name>. If the manifest URL
    drifts, the lookup silently measures an empty directory and every row reports
    0 MB forever -- which is precisely the blind state this replaced."""
    for dep in ("whisper", "embedder"):
        repo, size_mb = provision._hub_asset(dep)
        assert re.fullmatch(r"[\w.-]+/[\w.-]+", repo), f"{dep}: {repo!r} is not org/name"
        assert size_mb > 0
        cache = provision._hub_cache_dir(repo)
        assert cache is not None and cache.name == "models--" + repo.replace("/", "--")


def test_dir_bytes_survives_a_missing_or_racing_cache_dir():
    """Reporting progress must never be able to fail a download."""
    assert provision._dir_bytes(None) == 0
    assert provision._dir_bytes(Path("S:/nope/does/not/exist")) == 0


def test_hub_step_emits_a_heartbeat_while_the_loader_runs(monkeypatch, tmp_path):
    """The whole point: events keep arriving during a long step, so the wizard can
    tell movement from a hang."""
    monkeypatch.setattr(provision, "_HB_S", 0.01)
    monkeypatch.setattr(provision, "_hub_cache_dir", lambda repo: tmp_path)

    def slow_loader() -> None:
        import time as _t

        _t.sleep(0.2)

    events: list = []
    asyncio.run(provision._run_hub_step("embedder", slow_loader, on_event=events.append))

    working = [e for e in events if e["status"] == "working"]
    assert len(working) > 1, "no heartbeat -- the row would freeze again"
    assert all("bytes" in e for e in working)
    assert events[-1]["status"] == "done"


def test_hub_step_gives_up_at_the_ceiling(monkeypatch, tmp_path):
    """A loader that never returns must not hold the wizard open forever. The thread
    cannot be killed, so the contract is only that WE stop claiming to be working."""
    monkeypatch.setattr(provision, "_HB_S", 0.01)
    monkeypatch.setattr(provision, "_HUB_TIMEOUT_S", 0.05)
    monkeypatch.setattr(provision, "_hub_cache_dir", lambda repo: tmp_path)

    stop = asyncio.Event()

    def never_returns() -> None:
        import time as _t

        for _ in range(200):  # bounded so a failing test can't hang the suite
            if stop.is_set():
                return
            _t.sleep(0.01)

    events: list = []
    with pytest.raises(TimeoutError):
        asyncio.run(provision._run_hub_step("embedder", never_returns, on_event=events.append))
    stop.set()

    err = [e for e in events if e["status"] == "error"]
    assert err and err[-1]["kind"] == "stalled"
    assert err[-1]["retryable"] is True


def test_hub_step_reports_a_loader_failure_classified(monkeypatch, tmp_path):
    monkeypatch.setattr(provision, "_HB_S", 0.01)
    monkeypatch.setattr(provision, "_hub_cache_dir", lambda repo: tmp_path)

    def boom() -> None:
        raise OSError("dns error: No such host")

    events: list = []
    with pytest.raises(OSError):
        asyncio.run(provision._run_hub_step("whisper", boom, on_event=events.append))
    assert events[-1]["status"] == "error"
    assert events[-1]["kind"] == "no_network"
    # Never a raw trace on a public first run.
    assert "Traceback" not in events[-1]["message"]


def test_every_indeterminate_step_goes_through_the_guarded_runner():
    """The two hub loaders are the only steps with no byte progress of their own. If
    a future one is added straight to an `await asyncio.to_thread(...)`, it inherits
    the original bug, so pin that the walk calls the guarded path."""
    src = (_ROOT / "core" / "provision.py").read_text(encoding="utf-8")
    walk = src.split("async def provision(", 1)[1]
    assert "_run_hub_step" in walk
    for loader in ("_download_whisper", "_download_embedder"):
        assert f"asyncio.to_thread({loader})" not in walk, f"{loader} bypasses the guard"


# --- run.py must be able to record its own death ----------------------------


def test_the_log_is_opened_before_anything_that_can_fail_importing():
    """The log exists to catch a crash in a process with nowhere to print. It used to
    be set up after `from core import paths`, so the one failure it was for -- an
    import blowing up under pythonw -- died before the file was created, leaving an
    installed build with no logs directory at all."""
    src = (_ROOT / "run.py").read_text(encoding="utf-8")
    log_at = src.index("_log_dir.mkdir")
    for imp in ("from core import paths", "from tools import register_all"):
        assert log_at < src.index(imp), f"{imp!r} runs before the log is opened"


def test_a_native_crash_still_leaves_something_in_the_log():
    """torch/onnxruntime can take the process down without a Python traceback -- the
    log would just stop mid-line. faulthandler is what turns that into a stack."""
    src = (_ROOT / "run.py").read_text(encoding="utf-8")
    assert "faulthandler.enable(file=_log)" in src
    assert src.index("faulthandler.enable") < src.index("from core import paths")


def test_each_boot_is_marked_in_the_appended_log():
    """One appended file across every run: without a session boundary, "when did it
    die" is unanswerable."""
    assert "--- baby start " in (_ROOT / "run.py").read_text(encoding="utf-8")


# --- the shell must notice its backend exiting ------------------------------

_MAIN_RS = _ROOT / "ui" / "shell" / "src-tauri" / "src" / "main.rs"


def test_a_spawned_backend_is_watched():
    """attach_or_spawn runs once, at startup. Without a watcher a backend that dies
    mid-run leaves the window rendering stale content with no error, forever."""
    src = _MAIN_RS.read_text(encoding="utf-8")
    spawn = src.split("fn spawn_backend(", 1)[1].split("\nfn ", 1)[0]
    assert "watch_backend(" in spawn, "spawn_backend no longer starts the watcher"
    assert "fn watch_backend(" in src
    assert "try_wait()" in src


def test_a_deliberate_quit_is_not_reported_as_a_crash():
    """Quit takes the handle out of the mutex before killing it, so an empty slot has
    to mean 'shutting down' and stop the watcher -- otherwise every clean exit ends
    with a scary overlay."""
    src = _MAIN_RS.read_text(encoding="utf-8")
    watcher = src.split("fn watch_backend(", 1)[1].split("\n/// Overlay", 1)[0]
    assert "None => return" in watcher
    # And quit must still be the one that empties it.
    assert ".spawned.lock().unwrap().take()" in src


def test_the_death_notice_reaches_the_live_page_not_the_splash():
    """show_splash_message writes into the splash's `.wrap`, which is gone once the
    window has navigated to the backend-served UI -- exactly when this fires. It must
    inject its own overlay, and as text so a path can never be parsed as markup."""
    src = _MAIN_RS.read_text(encoding="utf-8")
    notice = src.split("fn show_backend_died(", 1)[1].split("\nfn ", 1)[0]
    assert "position:fixed" in notice
    assert "baby.log" in notice, "the notice must point at the log"
    # Comments discuss innerHTML; only the code matters.
    code = "\n".join(
        ln for ln in notice.splitlines() if not ln.lstrip().startswith("//")
    )
    assert "textContent" in code and "innerHTML" not in code


def test_the_manifest_labels_the_notice_points_at_still_exist():
    """The overlay tells the user setup resumes. That is only true while the steps are
    genuinely skip-if-present."""
    for dep in ("whisper", "embedder"):
        assert manifest.get(dep).assets[0].auto_downloads is True
