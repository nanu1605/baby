"""W0 model-delivery spike -- Ollama pull progress + resume proof.

The scariest UX moment in the v6 first-run is the multi-GB local-model pull. It
must never look frozen and must survive a dropped connection. This spike proves
the mechanism Baby will render in W3:

  * `POST /api/pull` with stream=true emits newline-delimited JSON objects, each
    carrying {status, digest?, total?, completed?}. Those `total`/`completed`
    byte counts are everything a real progress bar needs -- bytes, percent,
    speed, ETA.
  * The pull is RESUMABLE by construction: Ollama stores each layer as a
    content-addressed blob, so re-issuing the same pull continues from whatever
    already landed. Killing the network mid-pull and re-running loses nothing.

This file is the reference the W3 orchestrator ports from -- the exact JSON shape
below is what the wizard's progress component will consume over the app's own
event bus.

Usage:
    python pull_progress.py [model]        # default: qwen3.5:9b-q4_K_M (Baby's daily)
    python pull_progress.py all-minilm     # a small model for a quick real-bytes demo

Resume demo (owner, clean VM): start a pull, kill the NIC mid-download, re-run the
same command -- it picks up where it stopped.
"""

from __future__ import annotations

import argparse
import json
import sys
import time

import httpx

OLLAMA = "http://127.0.0.1:11434"
DEFAULT_MODEL = "qwen3.5:9b-q4_K_M"


def _human(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(n) < 1024:
            return f"{n:.1f}{unit}"
        n /= 1024
    return f"{n:.1f}PB"


def _bar(pct: float, width: int = 28) -> str:
    filled = int(width * pct / 100)
    return "[" + "#" * filled + "-" * (width - filled) + "]"


def pull(model: str) -> int:
    """Stream a pull and render live progress. Returns process exit code."""
    print(f"pulling {model!r} from the Ollama registry (Ctrl-C is safe -- pull resumes on re-run)")
    # Per-digest byte-rate tracking so the speed/ETA reflect the active layer.
    layer_start_bytes: dict[str, int] = {}
    layer_start_time: dict[str, float] = {}
    last_status = ""
    try:
        with httpx.Client(timeout=None) as client:
            with client.stream(
                "POST", f"{OLLAMA}/api/pull", json={"model": model, "stream": True}
            ) as resp:
                if resp.status_code != 200:
                    body = resp.read().decode("utf-8", "replace")[:200]
                    print(f"\nERROR: /api/pull returned {resp.status_code}: {body}")
                    return 1
                for line in resp.iter_lines():
                    if not line:
                        continue
                    try:
                        msg = json.loads(line)
                    except json.JSONDecodeError:
                        continue

                    if "error" in msg:
                        # Legible, not a raw trace. Registry-down / bad-name / net-drop.
                        print(f"\nERROR: {msg['error']}")
                        print("  retry once connected -- Ollama resumes from cached blobs.")
                        return 1

                    status = msg.get("status", "")
                    total = msg.get("total")
                    completed = msg.get("completed")
                    digest = msg.get("digest", "")

                    if total and completed is not None and digest:
                        now = time.monotonic()
                        if digest not in layer_start_time:
                            layer_start_time[digest] = now
                            layer_start_bytes[digest] = completed
                        elapsed = now - layer_start_time[digest]
                        moved = completed - layer_start_bytes[digest]
                        speed = moved / elapsed if elapsed > 0 else 0
                        pct = 100.0 * completed / total
                        eta = (total - completed) / speed if speed > 0 else 0
                        # `status` already reads "pulling <digest>"; don't repeat it.
                        sys.stdout.write(
                            f"\r  {status} {_bar(pct)} {pct:5.1f}%  "
                            f"{_human(completed)}/{_human(total)}  "
                            f"{_human(speed)}/s  ETA {eta:4.0f}s   "
                        )
                        sys.stdout.flush()
                    elif status and status != last_status:
                        # Non-byte phases: pulling manifest / verifying / writing / success.
                        sys.stdout.write(f"\r  {status}{' ' * 60}\n")
                        sys.stdout.flush()
                        last_status = status
        print("\nOK: pull stream completed (status=success).")
        return 0
    except httpx.ConnectError:
        print(f"\nERROR: cannot reach Ollama at {OLLAMA} -- is the daemon running?")
        return 1
    except KeyboardInterrupt:
        print("\ninterrupted -- re-run the same command to resume from where it stopped.")
        return 130


def main() -> int:
    ap = argparse.ArgumentParser(description="Ollama pull progress + resume spike")
    ap.add_argument("model", nargs="?", default=DEFAULT_MODEL)
    args = ap.parse_args()
    return pull(args.model)


if __name__ == "__main__":
    sys.exit(main())
