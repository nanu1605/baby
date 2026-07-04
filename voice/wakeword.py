"""openWakeWord detector: custom hey_baby.onnx with a built-in fallback.

The custom model is trained by the owner on Colab (scripts/wakeword_training.md)
and dropped into models/. Until then the pipeline runs on the pretrained
"hey_jarvis" model so everything stays demoable (DECISIONS.md).
Input contract: 16 kHz int16 mono in 1280-sample (80 ms) chunks.
"""

from __future__ import annotations

import time
from pathlib import Path

CHUNK = 1280  # 80 ms at 16 kHz


class WakeWord:
    def __init__(
        self,
        model_path: str | Path = "models/hey_baby.onnx",
        threshold: float = 0.55,
        builtin_fallback: str = "hey_jarvis",
        refractory_s: float = 2.0,
    ) -> None:
        self.model_path = Path(model_path)
        self.threshold = threshold
        self.builtin_fallback = builtin_fallback
        self.refractory_s = refractory_s
        self._model = None
        self._active_name = ""
        self._last_detection = 0.0

    def load(self) -> str:
        """Load the custom model if present, else the built-in fallback.

        Returns the active model name (surfaced in readiness notes).
        """
        from openwakeword.model import Model  # heavy; lazy

        if self.model_path.exists():
            self._model = Model(wakeword_models=[str(self.model_path)], inference_framework="onnx")
            self._active_name = self.model_path.stem
        else:
            self._model = Model(wakeword_models=[self.builtin_fallback], inference_framework="onnx")
            self._active_name = self.builtin_fallback
        return self._active_name

    @property
    def active_model(self) -> str:
        return self._active_name

    def detected(self, chunk_1280) -> bool:
        """Score one 80 ms chunk; True on threshold crossing (with refractory)."""
        if self._model is None:
            self.load()
        scores = self._model.predict(chunk_1280)
        score = max(scores.values()) if scores else 0.0
        if score < self.threshold:
            return False
        now = time.monotonic()
        if now - self._last_detection < self.refractory_s:
            return False
        self._last_detection = now
        self.reset()
        return True

    def reset(self) -> None:
        if self._model is not None:
            # clear rolling feature buffers so one phrase can't double-fire
            try:
                self._model.reset()
            except AttributeError:
                pass
