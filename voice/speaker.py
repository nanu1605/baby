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
    """verify(pcm16) -> (is_owner, similarity) against models/owner_voice.json."""

    def __init__(
        self,
        model_path: str | Path = "models/wespeaker_en_voxceleb_CAM++.onnx",
        profile_path: str | Path = "models/owner_voice.json",
        threshold: float = 0.5,
        extractor=None,
    ) -> None:
        self.model_path = Path(model_path)
        self.profile_path = Path(profile_path)
        self.threshold = float(threshold)
        self._extractor = extractor  # injectable for tests
        self._mean = None
        self.enabled = False
        self.note = "off (not loaded)"

    def load(self) -> str:
        """Load profile + model; fail-soft to enabled=False with a note."""
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

        if self._extractor is None:
            if not self.model_path.exists():
                self.note = "off (model file missing - re-run scripts/setup.ps1)"
                return self.note
            try:
                self._extractor = SherpaExtractor(str(self.model_path))
            except Exception as exc:  # noqa: BLE001 — sherpa failure degrades, never crashes
                self.note = f"off (sherpa-onnx failed to load: {exc})"
                return self.note

        dim = getattr(self._extractor, "dim", 0)
        if dim and len(mean) != dim:
            self.note = "off (profile dimension mismatch - re-enroll your voice)"
            return self.note

        self._mean = mean / (float(np.linalg.norm(mean)) + 1e-10)
        self.enabled = True
        self.note = f"on (threshold {self.threshold:g})"
        return self.note

    def verify(self, pcm16) -> tuple[bool, float]:
        """(is_owner, cosine similarity). Call only when enabled."""
        embedding = self._extractor.embed(pcm16)
        similarity = cosine(embedding, self._mean)
        return similarity >= self.threshold, similarity
