"""openWakeWord detector: custom "jarvis" model(s) alongside a built-in fallback.

The custom single-word "jarvis" model is trained by the owner on Colab
(scripts/wakeword_training.md) and dropped into models/. It runs SIDE BY SIDE
with the pretrained "hey_jarvis" model (openWakeWord scores every loaded model
per chunk at negligible CPU cost, and detected() takes the max) — so both
"Jarvis" and "Hey Jarvis" wake Baby, and wake never fully breaks even before the
custom model lands. Input contract: 16 kHz int16 mono in 1280-sample (80 ms)
chunks.
"""

from __future__ import annotations

import time
from pathlib import Path

CHUNK = 1280  # 80 ms at 16 kHz


class WakeWord:
    def __init__(
        self,
        model_path: str | Path = "models/jarvis.onnx",
        threshold: float = 0.55,
        builtin_fallback: str = "hey_jarvis",
        refractory_s: float = 2.0,
        extra_models: list[str] | None = None,
    ) -> None:
        self.model_path = Path(model_path)
        self.extra_models = [Path(m) for m in (extra_models or [])]
        self.threshold = threshold
        self.builtin_fallback = builtin_fallback
        self.refractory_s = refractory_s
        self._model = None
        self._active_name = ""
        self._last_detection = 0.0

    def load(self) -> str:
        """Load every present custom model PLUS the built-in fallback.

        Returns the active model name(s) joined with "+" (surfaced in readiness
        notes). detected() scores all of them and wakes on the highest.
        """
        from openwakeword.model import Model  # heavy; lazy

        refs: list[str] = []
        names: list[str] = []
        for path in [self.model_path, *self.extra_models]:
            if path.exists():
                refs.append(str(path))
                names.append(path.stem)
        # Always keep the pretrained fallback so "Hey Jarvis" works even with a
        # custom model loaded, and wake survives a missing/failed custom model.
        if self.builtin_fallback and self.builtin_fallback not in names:
            refs.append(self.builtin_fallback)
            names.append(self.builtin_fallback)
        self._model = Model(wakeword_models=refs, inference_framework="onnx")
        self._active_name = "+".join(dict.fromkeys(names))
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
