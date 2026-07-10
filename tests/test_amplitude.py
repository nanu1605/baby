"""V3e amplitude — pure RMS/quantize/throttle, the audio_io on_level callback,
and the additive event-kind membership. Frozen ground (router/provider/safety)
is untouched; this only exercises the new signal surface."""

from __future__ import annotations

import sys
import threading

import numpy as np
import pytest

from voice.amplitude import Throttle, quantize_level, rms_int16

# -- rms_int16 ----------------------------------------------------------------

def test_rms_silence_and_empty_are_zero():
    assert rms_int16(np.zeros(512, dtype=np.int16)) == 0.0
    assert rms_int16(np.array([], dtype=np.int16)) == 0.0
    assert rms_int16(None) == 0.0


def test_rms_full_scale_is_near_one():
    loud = np.full(512, 32767, dtype=np.int16)
    v = rms_int16(loud)
    assert 0.99 < v <= 1.0


def test_rms_is_bounded_and_monotone_with_amplitude():
    quiet = rms_int16((np.sin(np.linspace(0, 20, 512)) * 2000).astype(np.int16))
    loud = rms_int16((np.sin(np.linspace(0, 20, 512)) * 20000).astype(np.int16))
    assert 0.0 <= quiet < loud <= 1.0


# -- quantize_level -----------------------------------------------------------

def test_quantize_snaps_to_grid_and_clamps():
    assert quantize_level(0.02, step=0.05) == 0.0  # rounds down to 0
    assert quantize_level(0.07, step=0.05) == pytest.approx(0.05)
    assert quantize_level(0.13, step=0.05) == pytest.approx(0.15)
    assert quantize_level(9.9, step=0.05) == 1.0  # clamp high
    assert quantize_level(-1.0, step=0.05) == 0.0  # clamp low


def test_quantize_step_zero_is_passthrough():
    assert quantize_level(0.371, step=0) == 0.371


# -- Throttle -----------------------------------------------------------------

def test_throttle_allows_at_most_once_per_interval():
    t = Throttle(hz=15.0)  # interval ~66.7 ms
    assert t.ready(1.000) is True
    assert t.ready(1.010) is False  # 10 ms < interval
    assert t.ready(1.100) is True   # 100 ms >= interval
    assert t.ready(1.110) is False


# -- audio_io.play on_level callback ------------------------------------------

class _FakeStream:
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def write(self, block):
        pass


class _FakeSounddevice:
    def OutputStream(self, **kwargs):
        return _FakeStream()


def test_play_invokes_on_level_per_chunk(monkeypatch):
    monkeypatch.setitem(sys.modules, "sounddevice", _FakeSounddevice())
    from voice.audio_io import AudioIO

    sr = 24000  # 100 ms chunk = 2400 samples
    samples = (np.sin(np.linspace(0, 40, sr)) * 15000).astype(np.int16)  # 10 chunks
    levels: list[float] = []
    ok = AudioIO().play(samples, sr, threading.Event(), on_level=levels.append)

    assert ok is True
    assert len(levels) == 10  # one per 100 ms chunk
    assert all(0.0 <= v <= 1.0 for v in levels)
    assert any(v > 0.0 for v in levels)  # a real tone registers loudness


def test_play_without_on_level_is_unaffected(monkeypatch):
    monkeypatch.setitem(sys.modules, "sounddevice", _FakeSounddevice())
    from voice.audio_io import AudioIO

    samples = np.zeros(4800, dtype=np.int16)
    assert AudioIO().play(samples, 24000, threading.Event()) is True


def test_play_swallows_a_failing_callback(monkeypatch):
    monkeypatch.setitem(sys.modules, "sounddevice", _FakeSounddevice())
    from voice.audio_io import AudioIO

    def boom(_level):
        raise RuntimeError("callback must not kill playback")

    samples = np.full(4800, 5000, dtype=np.int16)
    # Should complete cleanly despite the raising callback.
    assert AudioIO().play(samples, 24000, threading.Event(), on_level=boom) is True


# -- additive event-kind membership -------------------------------------------

def test_amplitude_kinds_are_on_the_activity_allowlist():
    from ui.server import _ACTIVITY_KINDS

    assert "mic_rms" in _ACTIVITY_KINDS
    assert "tts_rms" in _ACTIVITY_KINDS
