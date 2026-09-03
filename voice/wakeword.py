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

import shutil
import time
from pathlib import Path

CHUNK = 1280  # 80 ms at 16 kHz


# --- where openWakeWord's own files live ------------------------------------
# Its wheel ships no weights and its downloader defaults to writing them INSIDE
# site-packages, which every `uv sync` throws away. So we keep our copy under
# core.paths.wakeword_dir() and hand openWakeWord explicit paths. Each helper
# degrades to openWakeWord's own default when our copy is absent, so a dev
# checkout that never provisioned behaves exactly as before.


def _package_dir() -> Path:
    """openWakeWord's built-in resources/models dir (the disposable copy)."""
    import openwakeword

    return Path(openwakeword.__file__).resolve().parent / "resources" / "models"


def _onnx_name(entry: dict) -> str:
    """Filename openWakeWord downloads for one MODELS/FEATURE_MODELS entry. The
    dict pins the .tflite name; the .onnx twin sits beside it under the same stem."""
    return Path(entry["model_path"]).with_suffix(".onnx").name


def adopt_package_models() -> int:
    """Copy any .onnx openWakeWord has in site-packages into the durable dir.

    Migration for an install provisioned before the durable dir existed: its files
    are still in site-packages until the next `uv sync` removes them, so lift them
    out while they are there. Needs no network, never overwrites, and leaves the
    package copy alone. Returns how many files it copied.
    """
    from core import paths

    src = _package_dir()
    if not src.is_dir():
        return 0
    dst = paths.wakeword_dir()
    copied = 0
    for f in sorted(src.glob("*.onnx")):
        target = dst / f.name
        if target.exists():
            continue
        dst.mkdir(parents=True, exist_ok=True)
        shutil.copy2(f, target)
        copied += 1
    return copied


def _pretrained_ref(name: str) -> str:
    """A pretrained wake word NAME -> our durable file if we have it, else the bare
    name (openWakeWord then resolves it inside its own package, as it always did)."""
    import openwakeword

    from core import paths

    entry = openwakeword.MODELS.get(name)
    if entry is None:
        return name  # not one of the six pretrained phrases; pass it through
    durable = paths.wakeword_dir() / _onnx_name(entry)
    return str(durable) if durable.exists() else name


def _feature_kwargs() -> dict:
    """melspectrogram + embedding paths for Model(), which loads them from its own
    package unless told otherwise. Only names the files we actually have."""
    import openwakeword

    from core import paths

    d = paths.wakeword_dir()
    out: dict[str, str] = {}
    for kwarg, key in (
        ("melspec_model_path", "melspectrogram"),
        ("embedding_model_path", "embedding"),
    ):
        entry = openwakeword.FEATURE_MODELS.get(key)
        if entry is None:
            continue
        f = d / _onnx_name(entry)
        if f.exists():
            out[kwarg] = str(f)
    return out


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

        adopt_package_models()  # one-time lift out of the disposable venv copy
        refs: list[str] = []
        names: list[str] = []
        for path in [self.model_path, *self.extra_models]:
            if path.exists():
                refs.append(str(path))
                names.append(path.stem)
        # Always keep the pretrained fallback so "Hey Jarvis" works even with a
        # custom model loaded, and wake survives a missing/failed custom model.
        if self.builtin_fallback and self.builtin_fallback not in names:
            refs.append(_pretrained_ref(self.builtin_fallback))
            names.append(self.builtin_fallback)
        # Never hand openWakeWord an empty list — Model([]) loads EVERY bundled
        # wake word (alexa, hey_mycroft, timer, weather…), so Baby would wake on
        # all of them. If a user blanked the fallback and has no custom model,
        # fall back to hey_jarvis rather than everything.
        if not refs:
            refs = [_pretrained_ref("hey_jarvis")]
            names = ["hey_jarvis"]
        self._model = Model(
            wakeword_models=refs, inference_framework="onnx", **_feature_kwargs()
        )
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
