"""Silero VAD wrapper: 512-sample (32 ms) frames at 16 kHz."""

from __future__ import annotations

FRAME = 512
SAMPLE_RATE = 16000


class VoiceDetector:
    def __init__(self, silence_ms: int = 400, threshold: float = 0.5) -> None:
        self.silence_ms = silence_ms
        self.threshold = threshold
        self._model = None
        self._silent_frames = 0
        self._frames_for_silence = max(1, int(silence_ms / (FRAME * 1000 / SAMPLE_RATE)))

    def load(self) -> None:
        from silero_vad import load_silero_vad  # heavy; lazy

        self._model = load_silero_vad()

    def probability(self, chunk_512) -> float:
        import torch

        if self._model is None:
            self.load()
        audio = torch.from_numpy(chunk_512.astype("float32") / 32768.0)
        return float(self._model(audio, SAMPLE_RATE).item())

    def is_speech(self, chunk_512, threshold: float | None = None) -> bool:
        return self.probability(chunk_512) >= (threshold or self.threshold)

    def utterance_done(self, chunk_512) -> bool:
        """Feed frames during capture; True after silence_ms of quiet."""
        if self.is_speech(chunk_512):
            self._silent_frames = 0
            return False
        self._silent_frames += 1
        return self._silent_frames >= self._frames_for_silence

    def reset(self) -> None:
        self._silent_frames = 0
        if self._model is not None:
            self._model.reset_states()
