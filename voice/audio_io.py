"""Microphone + speaker I/O over sounddevice (WASAPI shared mode).

Design rules (spec Section 13 + DECISIONS.md):
- The 16 kHz mono input stream NEVER closes — barge-in needs a hot mic
  while TTS is playing.
- Playback writes ~100 ms chunks and checks a stop event between chunks,
  so interruption lands within one chunk.
- Never request WASAPI exclusive mode; shared mode resamples transparently
  and coexists with other apps.
"""

from __future__ import annotations

import queue
import threading
from pathlib import Path

INPUT_RATE = 16000
CHUNK_MS = 100


class FrameBuffer:
    """Re-chunks arbitrary-size mic callbacks into fixed-size frames."""

    def __init__(self) -> None:
        self._buf = None

    def push(self, frame) -> None:
        import numpy as np

        flat = frame.reshape(-1)
        self._buf = flat.copy() if self._buf is None else np.concatenate([self._buf, flat])

    def pop(self, n: int):
        if self._buf is None or len(self._buf) < n:
            return None
        out, self._buf = self._buf[:n], self._buf[n:]
        return out

    def clear(self) -> None:
        self._buf = None


class AudioIO:
    """Persistent input stream + on-demand chunked output."""

    def __init__(
        self,
        samplerate: int = INPUT_RATE,
        input_device: int | None = None,
        output_device: int | None = None,
    ) -> None:
        self.samplerate = samplerate
        self.input_device = input_device
        self.output_device = output_device
        self._frames: queue.Queue = queue.Queue(maxsize=256)
        self._stream = None

    def start(self) -> None:
        import sounddevice as sd  # heavy; lazy

        def _callback(indata, frames, time_info, status) -> None:
            try:
                self._frames.put_nowait(indata.copy())
            except queue.Full:  # drop oldest — stale audio is worthless
                try:
                    self._frames.get_nowait()
                    self._frames.put_nowait(indata.copy())
                except (queue.Empty, queue.Full):
                    pass

        self._stream = sd.InputStream(
            samplerate=self.samplerate,
            channels=1,
            dtype="int16",
            device=self.input_device,
            blocksize=0,  # let PortAudio pick; FrameBuffer re-chunks
            callback=_callback,
        )
        self._stream.start()

    def read(self, timeout: float | None = 0.5):
        """Next mic frame (int16 ndarray) or None on timeout."""
        try:
            return self._frames.get(timeout=timeout)
        except queue.Empty:
            return None

    def drain(self) -> None:
        while True:
            try:
                self._frames.get_nowait()
            except queue.Empty:
                return

    def play(self, samples, samplerate: int, stop: threading.Event, on_level=None) -> bool:
        """Play int16 samples; returns False if stopped early (barge-in).

        ``on_level`` (optional) is called per ~100 ms chunk with that chunk's
        0..1 RMS — the honest TTS-amplitude source for the UI. Additive: absent
        callers are unaffected, and a failing callback never breaks playback.
        """
        import sounddevice as sd

        rms = None
        if on_level is not None:
            from voice.amplitude import rms_int16 as rms

        chunk = int(samplerate * CHUNK_MS / 1000)
        with sd.OutputStream(
            samplerate=samplerate, channels=1, dtype="int16", device=self.output_device
        ) as out:
            for start in range(0, len(samples), chunk):
                if stop.is_set():
                    return False
                block = samples[start : start + chunk]
                out.write(block)
                if rms is not None:
                    try:
                        on_level(rms(block))
                    except Exception:  # noqa: BLE001 — amplitude must never kill audio
                        pass
        return not stop.is_set()

    def beep(self) -> None:
        """Short wake-acknowledgement tone (non-fatal on any failure)."""
        try:
            import numpy as np

            t = np.linspace(0, 0.12, int(0.12 * 24000), endpoint=False)
            tone = (np.sin(2 * np.pi * 880 * t) * 0.3 * 32767).astype(np.int16)
            self.play(tone, 24000, threading.Event())
        except Exception:  # noqa: BLE001 — a silent beep must not kill the pipeline
            pass

    def close(self) -> None:
        if self._stream is not None:
            try:
                self._stream.stop()
                self._stream.close()
            finally:
                self._stream = None


def play_wav(path: str | Path) -> bool:
    """Play a WAV via winsound — independent of PortAudio (ready cue path)."""
    try:
        import winsound

        winsound.PlaySound(str(path), winsound.SND_FILENAME)
        return True
    except Exception:  # noqa: BLE001
        return False
