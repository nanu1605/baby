"""Speaker verification: sherpa-onnx CAM++ embeddings vs the enrolled owner.

Only the enrolled voice (Tanishq) may trigger actions; the check is a cosine
similarity between the utterance embedding and the enrolled MEAN embedding
from scripts/enroll_voice.py. CAM++ (wespeaker, 27 MB onnx) runs on CPU in
tens of milliseconds — chosen over SpeechBrain (no Windows support) and
Resemblyzer (stale since 2023). Fail-soft: a missing profile, missing model
file or a broken sherpa import turns verification OFF with a note — voice
keeps working exactly as in Phase 4.
"""

from __future__ import annotations

import json
import struct
from collections import deque
from pathlib import Path

SAMPLE_RATE = 16000


def cosine(a, b) -> float:
    """Cosine similarity of two vectors (numpy arrays or lists)."""
    import numpy as np

    va = np.asarray(a, dtype=np.float32)
    vb = np.asarray(b, dtype=np.float32)
    denom = float(np.linalg.norm(va)) * float(np.linalg.norm(vb))
    if denom == 0.0:
        return 0.0
    return float(np.dot(va, vb) / denom)


def pack_vector(vec) -> bytes:
    """Serialize a float vector to a float32 BLOB (mirror memory/store._pack).

    Shared by the v2 verifier and scripts/enroll_voice_v2.py so the DB round-trip
    has one source of truth. Dim is recoverable from len(blob) // 4 — speaker
    models vary in dim (CAM++ 512, ERes2Net/TitaNet 192), so nothing is fixed here.
    """
    v = [float(x) for x in vec]
    return struct.pack(f"{len(v)}f", *v)


def unpack_vector(blob: bytes) -> list[float]:
    """Inverse of pack_vector — count derived from the blob length, dim-agnostic."""
    return list(struct.unpack(f"{len(blob) // 4}f", blob))


def _preload_onnxruntime_dll() -> None:
    """Beat the System32 DLL shadow before sherpa loads.

    Windows ships a stale onnxruntime.dll (WindowsML, ORT 1.17) in System32,
    which outranks add_dll_directory in the pyd search order and segfaults
    sherpa (needs ORT C-API >= 24). ORT's own python module statically links
    the runtime, so merely importing onnxruntime does NOT put an
    "onnxruntime.dll" module in the process. Loading the venv copy explicitly
    does — and an already-loaded module always wins name resolution.
    """
    import ctypes
    from pathlib import Path

    try:
        import onnxruntime

        dll = Path(onnxruntime.__file__).parent / "capi" / "onnxruntime.dll"
        if dll.exists():
            ctypes.WinDLL(str(dll))
    except Exception:  # noqa: BLE001 — best-effort; sherpa raises clearly if it fails
        pass


class SherpaExtractor:
    """Thin sherpa-onnx wrapper: embed(int16 mono 16 kHz) -> list[float]."""

    def __init__(self, model_path: str, num_threads: int = 2) -> None:
        _preload_onnxruntime_dll()
        import sherpa_onnx  # heavy; lazy at call sites

        config = sherpa_onnx.SpeakerEmbeddingExtractorConfig(
            model=model_path, num_threads=num_threads
        )
        self._extractor = sherpa_onnx.SpeakerEmbeddingExtractor(config)
        self.dim = self._extractor.dim

    def embed(self, pcm16) -> list[float]:
        import numpy as np

        samples = pcm16.astype(np.float32) / 32768.0
        stream = self._extractor.create_stream()
        stream.accept_waveform(SAMPLE_RATE, samples)
        stream.input_finished()
        return list(self._extractor.compute(stream))


class SpeakerVerifier:
    """verify(pcm16) -> (is_owner, similarity) against the enrolled owner.

    v2: a profile is a SET of centroids (near/normal/far/turned mic positions or
    separate sessions) scored by MAX cosine — robust to the distance/energy
    variance that single-mean v1 false-rejected. Centroids come from the DB
    (speaker_profiles) when the caller supplies them; otherwise it falls back to
    the v1 single-mean models/owner_voice.json (kept working verbatim). Single-mean
    is just the one-centroid case, so scoring is unified.
    """

    def __init__(
        self,
        model_path: str | Path = "models/wespeaker_en_voxceleb_CAM++.onnx",
        profile_path: str | Path = "models/owner_voice.json",
        threshold: float = 0.5,
        extractor=None,
        centroids: list | None = None,
    ) -> None:
        self.model_path = Path(model_path)
        self.profile_path = Path(profile_path)
        self.threshold = float(threshold)
        self._extractor = extractor  # injectable for tests
        self._raw_centroids = centroids  # caller-supplied (DB), unnormalized vectors
        self._centroids: list = []  # normalized numpy vectors, set at load()
        self.enabled = False
        self.note = "off (not loaded)"

    def _ensure_extractor(self) -> str | None:
        """Resolve the embedding extractor; return a fail-soft note on failure."""
        if self._extractor is not None:
            return None
        if not self.model_path.exists():
            self.note = "off (model file missing - re-run scripts/setup.ps1)"
            return self.note
        try:
            self._extractor = SherpaExtractor(str(self.model_path))
        except Exception as exc:  # noqa: BLE001 — sherpa failure degrades, never crashes
            self.note = f"off (sherpa-onnx failed to load: {exc})"
            return self.note
        return None

    def load(self) -> str:
        """Load model + profile; fail-soft to enabled=False with a note.

        DB centroids (when supplied) take precedence over the JSON fallback.
        """
        if self._raw_centroids:
            return self._load_db_centroids()
        return self._load_json_profile()

    def _load_db_centroids(self) -> str:
        import numpy as np

        err = self._ensure_extractor()
        if err:
            return err
        dim = getattr(self._extractor, "dim", 0)
        kept = []
        for c in self._raw_centroids:
            v = np.asarray(c, dtype=np.float32)
            if v.ndim != 1 or not len(v):
                continue
            if dim and len(v) != dim:
                continue  # a centroid from a different model — skip it
            kept.append(v / (float(np.linalg.norm(v)) + 1e-10))
        if not kept:
            self.note = "off (profile dimension mismatch - re-enroll your voice)"
            return self.note
        self._centroids = kept
        self.enabled = True
        self.note = f"on ({len(kept)} centroids, threshold {self.threshold:g})"
        return self.note

    def _load_json_profile(self) -> str:
        """v1 single-mean path — behavior preserved verbatim."""
        import numpy as np

        if not self.profile_path.exists():
            self.note = "off (no enrollment - run scripts/enroll_voice.py)"
            return self.note
        try:
            profile = json.loads(self.profile_path.read_text(encoding="utf-8"))
            mean = np.asarray(profile["mean"], dtype=np.float32)
            if mean.ndim != 1 or not len(mean):
                raise ValueError("empty mean vector")
        except Exception as exc:  # noqa: BLE001 — a bad profile must not kill voice
            self.note = f"off (bad profile: {exc})"
            return self.note

        err = self._ensure_extractor()
        if err:
            return err

        dim = getattr(self._extractor, "dim", 0)
        if dim and len(mean) != dim:
            self.note = "off (profile dimension mismatch - re-enroll your voice)"
            return self.note

        self._centroids = [mean / (float(np.linalg.norm(mean)) + 1e-10)]
        self.enabled = True
        self.note = f"on (threshold {self.threshold:g})"
        return self.note

    def verify(self, pcm16) -> tuple[bool, float]:
        """(is_owner, best cosine similarity). Call only when enabled.

        Scores the utterance against every enrolled centroid and keeps the max —
        the owner near the mic and the owner across the room are both "the owner".
        """
        embedding = self._extractor.embed(pcm16)
        similarity = max(cosine(embedding, c) for c in self._centroids)
        return similarity >= self.threshold, similarity


class SessionTrust:
    """Rolling voice-trust over per-utterance cosine scores (B6, pure logic).

    Optimistic-demote: a session starts TRUSTED and drops to UNKNOWN (chat-only at
    the gate) only once the smoothed score stays at/below `reject` for
    `demote_after` consecutive utterances — no single shaky utterance can lock the
    owner out (v1's failure mode). UNCERTAIN is the band between reject and accept;
    the gate treats trusted and uncertain identically (allow-through, CONFIRM/DENY
    still hit the on-screen modal), so the tier distinction is display-only. A clear
    owner utterance (smoothed >= accept) recovers trust (hysteresis). PTT never
    reaches here — it auto-trusts upstream in the pipeline.
    """

    TRUSTED = "trusted"
    UNCERTAIN = "uncertain"
    UNKNOWN = "unknown"

    def __init__(
        self,
        accept: float = 0.62,
        reject: float = 0.45,
        window: int = 5,
        demote_after: int = 2,
    ) -> None:
        self.accept = float(accept)
        self.reject = float(reject)
        self.window = max(1, int(window))
        self.demote_after = max(1, int(demote_after))
        self._scores: deque = deque(maxlen=self.window)
        self._low_streak = 0
        self.tier = self.TRUSTED

    def reset(self) -> None:
        """New session — forget prior scores and start optimistic again."""
        self._scores.clear()
        self._low_streak = 0
        self.tier = self.TRUSTED

    @property
    def smoothed(self) -> float | None:
        if not self._scores:
            return None
        return sum(self._scores) / len(self._scores)

    def update(self, score: float) -> str:
        """Fold one utterance score in; return the new tier."""
        self._scores.append(float(score))
        avg = self.smoothed
        if avg <= self.reject:
            self._low_streak += 1
        else:
            self._low_streak = 0
        if avg >= self.accept:
            self.tier = self.TRUSTED  # clear owner presence recovers trust
        elif self._low_streak >= self.demote_after:
            self.tier = self.UNKNOWN  # sustained low → chat-only
        elif self.tier != self.UNKNOWN:
            self.tier = self.UNCERTAIN  # borderline; stay demoted once unknown
        return self.tier
