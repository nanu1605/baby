"""Enroll the owner's voice for speaker verification v2 (multi-centroid, B6).

The v1 single read-phrase enrollment (scripts/enroll_voice.py) built ONE mean
embedding, which false-rejected natural speech at a different mic distance/energy.
v2 records natural, conversational speech across guided mic positions (near /
normal / far / turned-away) and stores EACH clip as its own centroid in the
speaker_profiles DB table. Verification scores an utterance by MAX cosine over the
centroids, so "the owner near the mic" and "the owner across the room" are both
recognised.

Run from the repo root (one sitting, ~60-90s):
    uv run python scripts/enroll_voice_v2.py

Bench a candidate model (records against it, stores model-scoped centroids):
    uv run python scripts/enroll_voice_v2.py --model models/nemo_en_titanet_large.onnx

Add another session later (keeps existing centroids):
    uv run python scripts/enroll_voice_v2.py --append

Record a NON-owner test profile for the FAR side of the B7 report:
    uv run python scripts/enroll_voice_v2.py --label guest1
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from db.database import Database  # noqa: E402
from voice.speaker import (  # noqa: E402
    SAMPLE_RATE,
    SherpaExtractor,
    cosine,
    pack_vector,
)

# Guided mic positions — the axis of variance that broke v1. Each yields
# CLIPS_PER_POSITION centroids of natural speech.
POSITIONS = [
    ("near", "close to the mic (~20 cm), normal volume"),
    ("normal", "your usual seated distance"),
    ("far", "leaning back / across the room"),
    ("turned", "facing away from the mic, as if talking to someone else"),
]
CLIPS_PER_POSITION = 2
CLIP_SECONDS = 8.0

# A few natural prompts to say conversationally — the point is real speech, not a
# read phrase, so paraphrasing is encouraged.
SUGGESTIONS = [
    "Hey Baby, what's on my calendar for tomorrow morning?",
    "Baby, mujhe thodi der baad yaad dila dena, thank you.",
    "Can you open the browser and check the weather for me?",
    "Baby, take a screenshot and tell me what's on screen right now.",
]


def record(seconds: float):
    import numpy as np
    import sounddevice as sd

    frames = sd.rec(
        int(seconds * SAMPLE_RATE), samplerate=SAMPLE_RATE, channels=1, dtype="int16"
    )
    sd.wait()
    return np.squeeze(frames)


async def _run(args: argparse.Namespace) -> int:
    model_path = Path(args.model)
    if not model_path.exists():
        print(f"model not found: {model_path}")
        print("run scripts/setup.ps1 first (it downloads the speaker models).")
        return 1

    print("Loading speaker embedding model...")
    extractor = SherpaExtractor(str(model_path))
    print(f"model ready ({model_path.name}, embedding dim {extractor.dim}).")
    print()
    print("Speak NATURALLY at each position — full sentences, your real voice.")
    print(f"{len(POSITIONS)} positions x {args.clips} clips of {args.seconds:.0f}s each.")
    print()

    db = Database(args.db)
    await db.connect()
    try:
        if not args.append:
            await db.clear_speaker_profile(args.label, model_path.name)
            print(f"cleared any existing '{args.label}' centroids for {model_path.name}.")

        labelled: list[tuple[str, list[float]]] = []
        for pos, hint in POSITIONS:
            print(f"\n== position: {pos} — {hint} ==")
            for clip in range(1, args.clips + 1):
                suggestion = SUGGESTIONS[(len(labelled)) % len(SUGGESTIONS)]
                input(
                    f"  [{pos} {clip}/{args.clips}] Press Enter, then say "
                    f'(naturally): "{suggestion}"'
                )
                print("    recording...", end="", flush=True)
                time.sleep(0.2)  # keep the Enter keypress out of the recording
                pcm = record(args.seconds)
                emb = extractor.embed(pcm)
                labelled.append((pos, emb))
                await db.add_speaker_centroid(
                    args.label, model_path.name, extractor.dim, pack_vector(emb), pos
                )
                print(" done.")

        _report(labelled)
        print()
        print(
            f"stored {len(labelled)} centroids for '{args.label}' "
            f"(model {model_path.name})."
        )
        print(
            "set voice.speaker_verify.model to this file + enabled: true (and "
            "mode: observe for the B7 soak), then restart Baby."
        )
        return 0
    finally:
        await db.close()


def _report(labelled: list[tuple[str, list[float]]]) -> None:
    """Intra-speaker centroid cosine matrix + a suggested accept/reject band."""
    n = len(labelled)
    if n < 2:
        print("\n(only one centroid — record more for a coverage estimate.)")
        return
    print("\nIntra-speaker centroid similarity (higher = more consistent):")
    header = "        " + "  ".join(f"{p[:4]:>4}" for p, _ in labelled)
    print(header)
    off_diag: list[float] = []
    for i, (pi, ei) in enumerate(labelled):
        row = []
        for j, (_, ej) in enumerate(labelled):
            s = cosine(ei, ej)
            row.append(f"{s:>4.2f}")
            if i < j:
                off_diag.append(s)
        print(f"{pi[:6]:>6}  " + "  ".join(row))

    min_sim = min(off_diag)
    accept = max(0.35, round(min_sim - 0.05, 2))
    reject = max(0.20, round(min_sim - 0.20, 2))
    print()
    print(f"lowest intra-speaker centroid similarity: {min_sim:.2f}")
    print(
        "suggested starting band (tune from the B7 FAR/FRR report before enabling):"
    )
    print(f"  voice.speaker_verify.accept_threshold: {accept}")
    print(f"  voice.speaker_verify.reject_threshold: {reject}")
    if min_sim < 0.35:
        print("note: positions vary a lot — consider re-recording in a quieter room.")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default="baby.db", help="path to baby.db")
    parser.add_argument(
        "--model",
        default="models/wespeaker_en_voxceleb_CAM++.onnx",
        help="speaker-embedding onnx to enrol against (bench candidates supported)",
    )
    parser.add_argument(
        "--label", default="owner", help="profile label (use a name for FAR test voices)"
    )
    parser.add_argument(
        "--append", action="store_true", help="add to the existing profile (extra session)"
    )
    parser.add_argument(
        "--clips", type=int, default=CLIPS_PER_POSITION, help="clips per position"
    )
    parser.add_argument(
        "--seconds", type=float, default=CLIP_SECONDS, help="seconds per clip"
    )
    args = parser.parse_args()
    return asyncio.run(_run(args))


if __name__ == "__main__":
    sys.exit(main())
