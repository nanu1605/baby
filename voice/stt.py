"""Speech-to-text: faster-whisper large-v3-turbo, int8 on CPU.

CPU is deliberate (DECISIONS.md #42, #167): GPU Whisper needs CUDA runtime
libraries Baby does not ship, and the 9B LLM wants the VRAM when it loads.
The cost of a transcription is the encoder over a padded 30 s window, so it
barely depends on how long the user spoke. Measured on the 9700X for a 4 s
question: ~3.3 s and ~13 CPU-seconds on 4 threads with the single encoder
pass below, against ~5.6 s and ~44 CPU-seconds before (two passes, 8 threads).
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
        cpu_threads: int = 4,
        beam_size: int = 1,
        hotwords: str = "",
        local_files_only: bool = False,
    ) -> None:
        self.model_name = model
        self.device = device
        self.compute_type = compute_type
        self.cpu_threads = cpu_threads
        self.beam_size = beam_size
        # Names Whisper mishears with the owner's accent ("ollama" → "ullama");
        # passed as decoder bias every window, unlike initial_prompt.
        self.hotwords = hotwords
        # See Embedder: only provisioning's offline retry sets this.
        self.local_files_only = local_files_only
        self._model = None

    def load(self) -> None:
        from faster_whisper import WhisperModel  # heavy; lazy

        self._model = WhisperModel(
            self.model_name,
            device=self.device,
            compute_type=self.compute_type,
            cpu_threads=self.cpu_threads,
            local_files_only=self.local_files_only,
        )

    def transcribe(self, pcm16) -> tuple[str, str]:
        """int16 mono 16 kHz samples -> (text, language).

        The language is always "" -- see the comment on the call below for why
        it is no longer known here. Nothing acts on it: the router and the voice
        picker both read the script of the text itself.

        Returns ("", "") for silence, too-short audio, and known hallucinations,
        so the pipeline can drop the turn quietly.
        """
        import numpy as np

        if self._model is None:
            self.load()
        if len(pcm16) < SAMPLE_RATE * _MIN_SPEECH_S:
            return "", ""
        audio = pcm16.astype(np.float32) / 32768.0
        # One encoder pass, not two. With language=None, faster-whisper 1.2.1
        # runs detect_language() -- a full encoder pass -- and then throws that
        # output away, so generate_segments() encodes the same audio again
        # (transcribe.py, the `if language is None` branch and the
        # `encoder_output is None` check). The encoder is the whole cost, so every
        # utterance paid for it twice. multilingual=True detects the language from
        # the encoder output generate_segments already has, per segment, and a
        # language hint is what skips the up-front pass. The hint is not used for
        # decoding: a Hindi question still comes back in Devanagari (measured,
        # identical transcripts). It IS echoed back as info.language, which is why
        # this method no longer reports one.
        segments, _info = self._model.transcribe(
            audio,
            beam_size=self.beam_size,
            vad_filter=True,
            condition_on_previous_text=False,
            hotwords=self.hotwords or None,
            language="en",
            multilingual=True,
        )
        text = " ".join(seg.text.strip() for seg in segments).strip()
        if not text or text.lower() in _JUNK:
            return "", ""
        return text, ""
