"""Speech-to-text: faster-whisper large-v3-turbo, int8 on CPU.

CPU is deliberate (DECISIONS.md): the 9B LLM owns all 8 GB of VRAM, and
CTranslate2 disables int8 CUDA kernels on RTX 50-series anyway. int8 turbo
on the 9700X transcribes a 5-15 s utterance in ~1-2.5 s at zero VRAM.
"""

from __future__ import annotations

SAMPLE_RATE = 16000
_MIN_SPEECH_S = 0.3

# Whisper hallucinates these on silence/noise — never treat them as input.
_JUNK = frozenset(
    t.lower()
    for t in (
        "thank you.",
        "thank you very much.",
        "thanks for watching.",
        "thank you for watching.",
        "you",
        "bye.",
        "please subscribe.",
        "धन्यवाद।",
    )
)


class SpeechToText:
    def __init__(
        self,
        model: str = "large-v3-turbo",
        device: str = "cpu",
        compute_type: str = "int8",
        cpu_threads: int = 8,
        beam_size: int = 1,
        hotwords: str = "",
    ) -> None:
        self.model_name = model
        self.device = device
        self.compute_type = compute_type
        self.cpu_threads = cpu_threads
        self.beam_size = beam_size
        # Names Whisper mishears with the owner's accent ("ollama" → "ullama");
        # passed as decoder bias every window, unlike initial_prompt.
        self.hotwords = hotwords
        self._model = None

    def load(self) -> None:
        from faster_whisper import WhisperModel  # heavy; lazy

        self._model = WhisperModel(
            self.model_name,
            device=self.device,
            compute_type=self.compute_type,
            cpu_threads=self.cpu_threads,
        )

    def transcribe(self, pcm16) -> tuple[str, str]:
        """int16 mono 16 kHz samples -> (text, detected language code).

        Returns ("", lang) for silence, too-short audio, and known
        hallucinations, so the pipeline can drop the turn quietly.
        """
        import numpy as np

        if self._model is None:
            self.load()
        if len(pcm16) < SAMPLE_RATE * _MIN_SPEECH_S:
            return "", ""
        audio = pcm16.astype(np.float32) / 32768.0
        segments, info = self._model.transcribe(
            audio,
            beam_size=self.beam_size,
            vad_filter=True,
            condition_on_previous_text=False,
            hotwords=self.hotwords or None,
        )
        text = " ".join(seg.text.strip() for seg in segments).strip()
        if not text or text.lower() in _JUNK:
            return "", info.language or ""
        return text, info.language or ""
