"""Amplitude helpers (V3e) — pure RMS + quantize + throttle behind the honest
mic/TTS loudness signal the 3D core gauge rides.

Additive only: these feed two new event kinds (``mic_rms`` / ``tts_rms``) on the
existing ``/ws/activity`` stream. Nothing here touches the router, providers, or
safety gate. No audio/GL deps, so it unit-tests directly.
"""

from __future__ import annotations

import numpy as np

_INT16_MAX = 32768.0


def rms_int16(chunk) -> float:
    """Normalized 0..1 loudness of an int16 PCM chunk (RMS / int16 full scale).

    Empty / None → 0.0; clamped to [0, 1]. Never raises on odd input.
    """
    if chunk is None:
        return 0.0
    arr = np.asarray(chunk)
    if arr.size == 0:
        return 0.0
    x = arr.astype(np.float64)
    val = float(np.sqrt(np.mean(x * x)) / _INT16_MAX)
    if val <= 0.0:
        return 0.0
    return val if val < 1.0 else 1.0


def quantize_level(x: float, step: float = 0.05) -> float:
    """Snap a level to a coarse grid — kills jitter and downstream event churn."""
    if step <= 0:
        return x
    q = round(x / step) * step
    if q <= 0.0:
        return 0.0
    return q if q < 1.0 else 1.0


class Throttle:
    """Minimum-interval rate limiter. The event bus has no rate-limit, so a
    high-rate publisher must self-throttle at the source; ``ready(now)`` returns
    True at most once per ``1/hz`` seconds (monotonic ``now`` supplied by caller).
    """

    def __init__(self, hz: float = 15.0) -> None:
        self.min_interval = 1.0 / hz if hz > 0 else 0.0
        self._last: float | None = None

    def ready(self, now: float) -> bool:
        if self._last is None or (now - self._last) >= self.min_interval:
            self._last = now
            return True
        return False
