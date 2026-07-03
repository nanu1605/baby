"""Baby entrypoint: python run.py --cli | --ui | --voice | --all."""

from __future__ import annotations

import argparse
import asyncio
import sys

import tools.clock  # noqa: F401 — registers get_time

# Windows consoles may default to cp1252; Baby speaks UTF-8 (Hindi etc.).
# stdin matters too: piped input would otherwise decode Devanagari as mojibake.
# Streams can be None under pythonw.exe — skip those.
for _name in ("stdin", "stdout", "stderr"):
    _stream = getattr(sys, _name, None)
    if _stream is not None and (_stream.encoding or "").lower() != "utf-8":
        _stream.reconfigure(encoding="utf-8", errors="replace")


def main() -> None:
    parser = argparse.ArgumentParser(prog="baby", description="Baby — personal AI assistant")
    parser.add_argument("--cli", action="store_true", help="interactive REPL")
    parser.add_argument("--ui", action="store_true", help="web UI (Phase 1)")
    parser.add_argument("--voice", action="store_true", help="voice pipeline (Phase 3)")
    parser.add_argument("--all", action="store_true", help="everything (Phase 4)")
    args = parser.parse_args()

    if args.ui or args.voice or args.all:
        print("Not built yet — arrives in a later phase. Use --cli.")
        sys.exit(2)

    if args.cli:
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
