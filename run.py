"""Baby entrypoint: python run.py --cli | --ui | --voice | --all."""

from __future__ import annotations

import argparse
import asyncio
import faulthandler
import os
import sys
from datetime import datetime
from pathlib import Path

# Baby keeps its own log file, always.
#
# This runs BEFORE Baby's own imports on purpose. It used to sit after them, which
# meant the one failure it existed to record — an import blowing up inside a windowed
# process with nowhere to print — died before the file was ever created.
#
# It also used to open the file only when `sys.stdout is None`, which is what a
# windowed CPython hands you. An INSTALLED build does not go through one: uv's venv
# pythonw.exe is a trampoline that re-execs the base console python.exe and gives it
# live stdio objects whose output goes nowhere at all. The test passed, the file was
# never opened, and a whole install ran without producing a single log line — which
# is also why cffi fell back to a "Python-CFFI error" MessageBox instead of writing
# a traceback anyone could read. So there is nothing to detect any more: keep the
# file unconditionally and tee a console run into it as well.
#
# faulthandler covers the other half: a native crash in torch or onnxruntime kills
# the process without a Python traceback, so without it the log stops mid-line.
_log_dir = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "baby" / "logs"
_log_dir.mkdir(parents=True, exist_ok=True)
_log_path = _log_dir / "baby.log"
# One appended file across every run. A download progress bar writes thousands of
# \r updates, and those now land here on a console run too, so roll the file once
# it gets big rather than letting it grow without bound.
try:
    if _log_path.stat().st_size > 5_000_000:
        _log_path.replace(_log_dir / "baby.log.1")
except OSError:  # first run, or another Baby holds it — neither is worth failing on
    pass
_log = open(_log_path, "a", encoding="utf-8", buffering=1)  # noqa: SIM115


class _Tee:
    """Write to the real stream AND the log.

    Only used when a real stream exists. Writes to it are best-effort: the whole
    reason this file exists is that Baby's console can be dead or absent, and a
    dying console must never take the log down with it.
    """

    def __init__(self, stream, log) -> None:
        self._stream = stream
        self._log = log

    def write(self, s):
        try:
            self._stream.write(s)
        except Exception:  # noqa: BLE001 — a dead console is the expected case
            pass
        return self._log.write(s)

    def flush(self) -> None:
        try:
            self._stream.flush()
        except Exception:  # noqa: BLE001
            pass
        self._log.flush()

    def isatty(self) -> bool:
        try:
            return self._stream.isatty()
        except Exception:  # noqa: BLE001
            return False

    def __getattr__(self, name):
        # encoding/reconfigure/fileno and friends belong to the real stream; the
        # log is already UTF-8 and needs none of them. Guard the two own slots so a
        # lookup that arrives before __init__ finishes raises rather than recursing.
        if name in ("_stream", "_log"):
            raise AttributeError(name)
        return getattr(self._stream, name)


sys.stdout = _log if sys.stdout is None else _Tee(sys.stdout, _log)
sys.stderr = _log if sys.stderr is None else _Tee(sys.stderr, _log)

faulthandler.enable(file=_log)

# Mark where each boot begins — otherwise "when did it die" is unanswerable from a
# file with no session boundaries.
_log.write(f"\n--- baby start {datetime.now().isoformat(timespec='seconds')} ---\n")

from core import paths  # noqa: E402 -- must not precede the log setup above
from tools import register_all  # noqa: E402

# Windows consoles may default to cp1252; Baby speaks UTF-8 (Hindi etc.).
# stdin matters too: piped input would otherwise decode Devanagari as mojibake.
for _name in ("stdin", "stdout", "stderr"):
    _stream = getattr(sys, _name, None)
    if _stream is not None and (getattr(_stream, "encoding", "") or "").lower() != "utf-8":
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, OSError, ValueError):
            # Not a real text stream, or already detached. Mojibake on a console
            # nobody can read is not worth refusing to boot over -- and the log is
            # UTF-8 either way, which is where the text has to survive.
            pass


def main() -> None:
    from dotenv import load_dotenv

    load_dotenv(paths.env_path())  # keys from .env (BABY_HOME when installed, else cwd)
    register_all()
    parser = argparse.ArgumentParser(prog="baby", description="Baby — personal AI assistant")
    parser.add_argument("--cli", action="store_true", help="interactive REPL")
    parser.add_argument("--ui", action="store_true", help="web UI (Phase 1)")
    parser.add_argument("--voice", action="store_true", help="voice pipeline (Phase 3)")
    parser.add_argument("--all", action="store_true", help="everything (Phase 4)")
    args = parser.parse_args()

    if args.all and not (args.ui or args.voice):
        args.ui = args.voice = True

    if args.ui or args.voice:
        import yaml

        from ui.server import run_ui

        with open(paths.ensure_config(), encoding="utf-8") as f:
            config = yaml.safe_load(f)
        config = paths.apply_setup(config)  # overlay first-run wizard choices (v6)
        try:
            # --voice boots the UI stack too (same process, spec section 16);
            # voice attaches on top and fails soft back to text-only.
            code = asyncio.run(run_ui(config, with_voice=args.voice))
        except KeyboardInterrupt:
            print("\nbye.")
        else:
            # Non-zero means the first-run wizard stamped a router mode only a fresh
            # boot can honour, and the shell that spawned us is watching for exactly
            # this status so it can bring us back. See ui/server.py RESTART_EXIT_CODE.
            if code:
                sys.exit(code)
    elif args.cli:
        from clients.cli import run_cli

        try:
            asyncio.run(run_cli())
        except KeyboardInterrupt:
            # Ctrl+C mid-stream cancels the task and re-raises here (3.11+);
            # the banner promises a clean exit, not a traceback.
            print("\nbye.")
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
