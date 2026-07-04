"""The audible "Baby ready" cue (spec Section 13, owner requirement).

Full cue plays the WAV pre-rendered by setup.ps1 — instant and independent
of TTS cold-start. Degraded (voice failed but text works) → Windows chime +
toast. Never announce ready when the model can't actually respond — that
gate lives in the caller (ready_check), not here. Throttled so a crash/
reconnect loop can't chant "Baby ready" endlessly.
"""

from __future__ import annotations

import time
from pathlib import Path


class ReadyCue:
    def __init__(self, cfg: dict) -> None:
        announce = cfg.get("ready_announce", {})
        self.enabled = bool(announce.get("enabled", True))
        self.sound_file = Path(announce.get("sound_file", "assets/baby_ready.wav"))
        self.min_interval_s = float(announce.get("min_interval_s", 60))
        self._last_played = 0.0

    def _throttled(self) -> bool:
        now = time.monotonic()
        if now - self._last_played < self.min_interval_s:
            return True
        self._last_played = now
        return False

    def play_full(self) -> bool:
        """Spoken cue from the cached WAV. False if skipped or failed."""
        if not self.enabled:
            return False
        if not self.sound_file.exists():
            # decide the path BEFORE consuming the throttle, or the degraded
            # fallback would be silenced by the very call that chose it
            return self.play_degraded()
        if self._throttled():
            return False
        from voice.audio_io import play_wav

        return play_wav(self.sound_file)

    def play_degraded(self) -> bool:
        """Chime + toast when voice is down but text works."""
        if not self.enabled or self._throttled():
            return False
        try:
            import winsound

            winsound.MessageBeep(winsound.MB_ICONASTERISK)
            return True
        except Exception:  # noqa: BLE001 — a silent cue must not block startup
            return False
