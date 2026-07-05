"""Enroll the owner's voice for speaker verification.

Records 6 short utterances (mixed EN/HI/Hinglish - the CAM++ model is
English-trained, mixed enrollment pulls the mean toward real usage), embeds
each with sherpa-onnx, prints the intra-speaker similarity matrix plus a
suggested threshold, and writes models/owner_voice.json.

Run from the repo root:  uv run python scripts/enroll_voice.py
"""

from __future__ import annotations

import json
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from voice.speaker import SAMPLE_RATE, SherpaExtractor, cosine  # noqa: E402

MODEL_PATH = Path("models/wespeaker_en_voxceleb_CAM++.onnx")
PROFILE_PATH = Path("models/owner_voice.json")
RECORD_SECONDS = 4.0

PROMPTS = [
    "Hey Baby, what is the weather like in Indore today?",
    "Open the browser and search for the latest tech news.",
    "Baby, aaj ka mausam kaisa hai? Mujhe batao.",
    "Mere liye ek background task shuru karo abhi.",
    "Baby, close Spotify and take a screenshot please.",
    "Kal subah 8 baje mujhe yaad dilana, thank you Baby.",
]


def record(seconds: float):
    import numpy as np
    import sounddevice as sd

    frames = sd.rec(
        int(seconds * SAMPLE_RATE), samplerate=SAMPLE_RATE, channels=1, dtype="int16"
    )
    sd.wait()
    return np.squeeze(frames)


def main() -> int:
    if not MODEL_PATH.exists():
        print(f"model not found: {MODEL_PATH}")
        print("run scripts/setup.ps1 first (it downloads the speaker model).")
        return 1

    print("Loading speaker embedding model...")
    extractor = SherpaExtractor(str(MODEL_PATH))
    print(f"model ready (embedding dim {extractor.dim}).")
    print()
    print(f"You will record {len(PROMPTS)} short phrases ({RECORD_SECONDS:.0f}s each).")
    print("Speak naturally, at your normal distance from the microphone.")
    print()

    embeddings: list[list[float]] = []
    for i, prompt in enumerate(PROMPTS, 1):
        input(f"[{i}/{len(PROMPTS)}] Press Enter, then say: \"{prompt}\"")
        print("  recording...", end="", flush=True)
        time.sleep(0.2)  # keep the Enter keypress out of the recording
        pcm = record(RECORD_SECONDS)
        embeddings.append(extractor.embed(pcm))
        print(" done.")

    print()
    print("Intra-speaker similarity matrix (higher = more consistent):")
    sims: list[float] = []
    for i in range(len(embeddings)):
        row = []
        for j in range(len(embeddings)):
            s = cosine(embeddings[i], embeddings[j])
            row.append(f"{s:.2f}")
            if i < j:
                sims.append(s)
        print("  " + "  ".join(row))

    min_sim = min(sims)
    suggested = max(0.25, round(min_sim - 0.10, 2))
    print()
    print(f"lowest intra-speaker similarity: {min_sim:.2f}")
    print(f"suggested config threshold (voice.speaker_verify.threshold): {suggested}")
    if min_sim < 0.45:
        print("note: your recordings vary a lot - consider re-running in a quiet room.")

    import numpy as np

    mean = np.mean(np.asarray(embeddings, dtype=np.float32), axis=0)
    profile = {
        "model": MODEL_PATH.name,
        "dim": extractor.dim,
        "created_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "embeddings": [list(map(float, e)) for e in embeddings],
        "mean": [float(x) for x in mean],
    }
    PROFILE_PATH.write_text(json.dumps(profile), encoding="utf-8")
    print()
    print(f"profile written: {PROFILE_PATH}")
    print("restart Baby - the boot log should say speaker verify: on.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
