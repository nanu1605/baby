"""Silero VAD wrapper: 512-sample (32 ms) frames at 16 kHz."""

from __future__ import annotations

FRAME = 512
SAMPLE_RATE = 16000


class VoiceDetector:
    def __init__(
        self, silence_ms: int = 400, threshold: float = 0.5, speech_wait_ms: int = 5000
    ) -> None:
        self.silence_ms = silence_ms
        self.threshold = threshold
        self.speech_wait_ms = speech_wait_ms
        self._model = None
        self._silent_frames = 0
        self._speech_seen = False
        frame_ms = FRAME * 1000 / SAMPLE_RATE
        self._frames_for_silence = max(1, int(silence_ms / frame_ms))
        self._frames_for_wait = max(1, int(speech_wait_ms / frame_ms))

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

    @property
    def speech_started(self) -> bool:
        """True once any speech frame was seen since the last reset()."""
        return self._speech_seen

    def utterance_done(self, chunk_512) -> bool:
        """Feed frames during capture; True after silence_ms of quiet.

        Silence only ends an utterance AFTER speech has started — the pause
        between the wake beep and the user's first word must not end the
        capture (it did: "replied only the first time" owner bug). Pure
        silence gives up after speech_wait_ms instead.
        """
        if self.is_speech(chunk_512):
            self._speech_seen = True
            self._silent_frames = 0
            return False
        self._silent_frames += 1
        if not self._speech_seen:
            return self._silent_frames >= self._frames_for_wait
        return self._silent_frames >= self._frames_for_silence

    def reset(self) -> None:
        self._silent_frames = 0
        self._speech_seen = False
        if self._model is not None:
            self._model.reset_states()
