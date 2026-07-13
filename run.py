"""Baby entrypoint: python run.py --cli | --ui | --voice | --all."""

from __future__ import annotations

import argparse
import asyncio
import sys

from core import paths
from tools import register_all

# Under pythonw.exe (autostart, hidden) stdout/stderr are None — Task
# Scheduler can't redirect them, so Baby owns its log file instead.
if sys.stdout is None or sys.stderr is None:
    import os
    from pathlib import Path

    _log_dir = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "baby" / "logs"
    _log_dir.mkdir(parents=True, exist_ok=True)
    _log = open(_log_dir / "baby.log", "a", encoding="utf-8", buffering=1)  # noqa: SIM115
    if sys.stdout is None:
        sys.stdout = _log
    if sys.stderr is None:
        sys.stderr = _log

# Windows consoles may default to cp1252; Baby speaks UTF-8 (Hindi etc.).
# stdin matters too: piped input would otherwise decode Devanagari as mojibake.
for _name in ("stdin", "stdout", "stderr"):
    _stream = getattr(sys, _name, None)
    if _stream is not None and (getattr(_stream, "encoding", "") or "").lower() != "utf-8":
        _stream.reconfigure(encoding="utf-8", errors="replace")


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
            asyncio.run(run_ui(config, with_voice=args.voice))
        except KeyboardInterrupt:
            print("\nbye.")
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
