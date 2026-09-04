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
import os
import re
import subprocess
import sys
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
    monkeypatch.setattr(provision, "_STEP_TIMEOUT_S", 0.05)
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


# --- the wake-word step ------------------------------------------------------
# Reported as "the installation is stuck at Wake-word models (openWakeWord) - 19 MB".
# It was not stuck. 19 MB of GitHub release assets took 369s on the reporting machine,
# one file every 30-60s, and the step said nothing for the whole of it -- so a healthy
# six minutes and a wedged one rendered identically. Unlike the hub steps this one now
# writes into a directory we chose (core.paths.wakeword_dir), so the bytes are ours to
# count.


def test_the_wake_word_step_reports_progress_while_it_downloads(monkeypatch, tmp_path):
    monkeypatch.setattr(provision, "_HB_S", 0.01)
    target = tmp_path / "openwakeword"
    target.mkdir()

    def drip() -> None:
        import time as _t

        for i in range(4):
            (target / f"m{i}.onnx").write_bytes(b"x" * 200_000)
            _t.sleep(0.05)

    monkeypatch.setattr(provision, "_download_openwakeword", lambda t: drip())
    events: list = []
    asyncio.run(provision._run_wakeword_step(target, on_event=events.append))

    working = [e for e in events if e["status"] == "working"]
    assert len(working) > 1, "no heartbeat -- the row would look stuck again"
    assert [e["bytes"] for e in working] == sorted(e["bytes"] for e in working)
    assert working[-1]["bytes"] > working[0]["bytes"], "the bytes never moved"
    assert events[-1]["status"] == "done"
    assert events[-1]["detail"] == "wake-word models ready"


def test_the_wake_word_row_says_how_far_along_it_is(monkeypatch, tmp_path):
    """The row's text has to carry the number. "working" is what it said before."""
    monkeypatch.setattr(provision, "_HB_S", 0.01)
    target = tmp_path / "openwakeword"
    target.mkdir()

    def drip() -> None:
        import time as _t

        (target / "a.onnx").write_bytes(b"x" * (8 * 1024 * 1024))
        _t.sleep(0.08)

    monkeypatch.setattr(provision, "_download_openwakeword", lambda t: drip())
    events: list = []
    asyncio.run(provision._run_wakeword_step(target, on_event=events.append))
    details = [e["detail"] for e in events if e["status"] == "working"]
    assert any(re.search(r"\d+ of ~\d+ MB", d) for d in details), details


def test_the_wake_word_step_gives_up_at_the_ceiling(monkeypatch, tmp_path):
    """Shared with the hub steps, but pinned here too: this is the row a user sat in
    front of for six minutes, and an unbounded one has no end condition at all."""
    monkeypatch.setattr(provision, "_HB_S", 0.01)
    monkeypatch.setattr(provision, "_STEP_TIMEOUT_S", 0.05)

    stop = asyncio.Event()

    def never_returns() -> None:
        import time as _t

        for _ in range(200):  # bounded so a failing test can't hang the suite
            if stop.is_set():
                return
            _t.sleep(0.01)

    monkeypatch.setattr(provision, "_download_openwakeword", lambda target: never_returns())
    events: list = []
    with pytest.raises(TimeoutError):
        asyncio.run(provision._run_wakeword_step(tmp_path, on_event=events.append))
    stop.set()

    err = [e for e in events if e["status"] == "error"]
    assert err and err[-1]["retryable"] is True


def test_every_indeterminate_step_goes_through_the_guarded_runner():
    """No step in the walk may block on a loader nobody is narrating.

    This test used to name the two hub loaders it knew about, and the wake-word
    download was added straight to an `await asyncio.to_thread(...)` beside them --
    inheriting the exact bug the list was written to prevent, and reported months
    later as a six-minute hang. So enumerate instead of listing: every threaded call
    in the walk has to be either the guarded runner or the bounded verify.
    """
    src = (_ROOT / "core" / "provision.py").read_text(encoding="utf-8")
    walk = src.split("async def provision(", 1)[1]
    threaded = re.findall(r"asyncio\.to_thread\(\s*([\w.]+)", walk)
    # health.run_all is the verify step: bounded, and it owns its own row.
    assert threaded == ["health.run_all"], (
        f"{threaded} runs unwatched in the walk -- see provision._run_watched_step"
    )
    assert "_run_hub_step" in walk and "_run_wakeword_step" in walk


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


def _run_entrypoint(tmp_path, timeout=300):
    """`run.py` with no arguments: prints help, exits 0, touches nothing heavy.

    LOCALAPPDATA is redirected so the log lands in tmp_path rather than the real one.
    """
    env = dict(os.environ, LOCALAPPDATA=str(tmp_path))
    return subprocess.run(
        [sys.executable, "run.py"],
        cwd=_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def test_a_run_with_a_working_console_still_leaves_a_log(tmp_path):
    """The log used to be opened only when `sys.stdout is None`.

    That is what a windowed CPython hands you, and it is never what an INSTALLED
    build gets: uv's venv pythonw.exe is a trampoline that re-execs the base CONSOLE
    python.exe and gives it live stdio objects whose output goes nowhere. The check
    passed, the file was never opened, and a whole install ran without producing a
    single line -- which is also why cffi resorted to a "Python-CFFI error"
    MessageBox instead of a traceback. Nothing about a usable stream is detectable
    from in here, so the file is now kept unconditionally.

    This runs with a real console (pytest has one) and demands BOTH halves: the
    caller still sees the output, and the log has it too.
    """
    r = _run_entrypoint(tmp_path)
    assert r.returncode == 0, r.stderr
    assert "usage: baby" in r.stdout, "the tee swallowed the console it was teeing"

    log = tmp_path / "baby" / "logs" / "baby.log"
    assert log.exists(), "a run with a live console left no log at all"
    body = log.read_text(encoding="utf-8", errors="replace")
    assert "--- baby start " in body
    assert "usage: baby" in body, "stdout never reached the log"


def test_a_dead_console_does_not_take_the_log_with_it(tmp_path):
    """The stream Baby tees into can be closed under it -- that is the whole reason
    the file exists. A write that raises must be swallowed, not propagated, or the
    log dies exactly when it is needed."""
    r = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import runpy, sys\n"
                "sys.argv = ['run.py']\n"
                "import io\n"
                "class Dead(io.StringIO):\n"
                "    def write(self, s): raise OSError('console is gone')\n"
                "    def flush(self): raise OSError('console is gone')\n"
                "sys.stdout = Dead()\n"
                "runpy.run_path('run.py', run_name='__main__')\n"
            ),
        ],
        cwd=_ROOT,
        env=dict(os.environ, LOCALAPPDATA=str(tmp_path)),
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert r.returncode == 0, r.stderr
    body = (tmp_path / "baby" / "logs" / "baby.log").read_text(encoding="utf-8", errors="replace")
    assert "usage: baby" in body, "a dead console lost the output the log was for"


def test_the_log_is_rolled_before_it_can_grow_without_bound(tmp_path):
    """Teeing a console run in means download progress bars now land here too --
    thousands of \\r updates per model. Appending forever with no ceiling would turn
    the one diagnostic Baby owns into the thing filling the user's disk."""
    logs = tmp_path / "baby" / "logs"
    logs.mkdir(parents=True)
    (logs / "baby.log").write_bytes(b"OLD" + b"x" * 6_000_000)

    assert _run_entrypoint(tmp_path).returncode == 0
    assert (logs / "baby.log.1").read_bytes()[:3] == b"OLD", "the old log was not kept"
    assert (logs / "baby.log").stat().st_size < 100_000, "the log was not rolled"


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
    assert "baby.log" in notice, "the notice must point at the log"
    assert "show_overlay(" in notice, "the notice no longer reaches the live page"
    # The overlay itself is shared with the restart notice; the properties that make
    # it safe belong to it, not to either caller.
    overlay = src.split("fn show_overlay(", 1)[1].split("\nfn ", 1)[0]
    assert "position:fixed" in overlay
    assert "show_splash_message" not in overlay
    # Comments discuss innerHTML; only the code matters.
    code = "\n".join(ln for ln in overlay.splitlines() if not ln.lstrip().startswith("//"))
    assert "textContent" in code and "innerHTML" not in code


def test_the_manifest_labels_the_notice_points_at_still_exist():
    """The overlay tells the user setup resumes. That is only true while the steps are
    genuinely skip-if-present."""
    for dep in ("whisper", "embedder"):
        assert manifest.get(dep).assets[0].auto_downloads is True


# --- a dead transfer must not wait out the elapsed ceiling -------------------
# Measured on a clean VM: the network was cut mid-download and then RESTORED, and
# the step sat dead for 25 more minutes with the row reading "no new data for
# 23m" while the guest was pinging huggingface.co at 32ms. An interrupted hub
# download does not resume itself; only reopening Baby did. Before this the row
# would have held that pose until _STEP_TIMEOUT_S -- a full hour -- because stall
# detection changed the wording and nothing else.


def _stall_probe(values):
    """A probe that yields each value once, then repeats the last one forever."""
    seq = list(values)

    def probe():
        return seq.pop(0) if len(seq) > 1 else seq[0]

    return probe


@pytest.mark.parametrize("dep", ["whisper", "embedder"])
def test_a_stalled_download_gives_up_long_before_the_elapsed_ceiling(monkeypatch, dep):
    monkeypatch.setattr(provision, "_HB_S", 0.01)
    monkeypatch.setattr(provision, "_STALL_CEILING_S", 0.05)
    monkeypatch.setattr(provision, "_STEP_TIMEOUT_S", 3600)  # untouched: must not be what fires

    stop = asyncio.Event()

    def never_returns() -> None:
        import time as _t

        for _ in range(400):  # bounded so a failing test can't hang the suite
            if stop.is_set():
                return
            _t.sleep(0.01)

    events: list = []
    with pytest.raises(TimeoutError):
        asyncio.run(provision._run_watched_step(
            dep,
            never_returns,
            probe=lambda: 1,  # one byte, and it never moves
            detail=lambda seen, elapsed, stalled: "stuck",
            total_bytes=100_000_000,
            on_event=events.append,
        ))
    stop.set()

    err = [e for e in events if e["status"] == "error"]
    assert err, "the step gave up without telling anyone"
    assert err[-1]["kind"] == "stalled"
    assert err[-1]["retryable"] is True


def test_a_slow_model_load_is_not_mistaken_for_a_stall(monkeypatch):
    """Past ~95% the loader builds the model in memory and the cache stops growing.
    That is the one time zero bytes is NORMAL, and killing it would trade a hang
    for a worse bug -- a working install refused because the machine is slow."""
    monkeypatch.setattr(provision, "_HB_S", 0.01)
    monkeypatch.setattr(provision, "_STALL_CEILING_S", 0.05)

    def finishes() -> None:
        import time as _t

        _t.sleep(0.4)  # 8x the stall ceiling, entirely inside the load phase

    events: list = []
    asyncio.run(provision._run_watched_step(
        "whisper",
        finishes,
        probe=lambda: 100_000_000,  # fully downloaded; bytes legitimately static
        detail=lambda seen, elapsed, stalled: "loading the model",
        total_bytes=100_000_000,
        on_event=events.append,
    ))
    assert events[-1]["status"] == "done"
    assert not [e for e in events if e["status"] == "error"]


def test_an_unknown_total_leaves_the_stall_ceiling_disarmed(monkeypatch):
    """total_bytes=0 means we cannot tell downloading from loading, so only the
    elapsed ceiling applies -- never guess a step to death."""
    monkeypatch.setattr(provision, "_HB_S", 0.01)
    monkeypatch.setattr(provision, "_STALL_CEILING_S", 0.05)

    def finishes() -> None:
        import time as _t

        _t.sleep(0.3)

    events: list = []
    asyncio.run(provision._run_watched_step(
        "whisper",
        finishes,
        probe=lambda: 1,  # static, and would trip the ceiling if it were armed
        detail=lambda seen, elapsed, stalled: "x",
        on_event=events.append,
    ))
    assert events[-1]["status"] == "done"


def test_both_real_steps_arm_the_stall_ceiling():
    """A step that measures bytes but passes no total gets the old behaviour
    silently, so pin that the two real callers hand their size over."""
    src = (_ROOT / "core" / "provision.py").read_text(encoding="utf-8")
    for fn in ("_run_hub_step", "_run_wakeword_step"):
        body = src.split(f"async def {fn}(", 1)[1].split("\nasync def ", 1)[0]
        assert "total_bytes=" in body, f"{fn} leaves the stall ceiling disarmed"
