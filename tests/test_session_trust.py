"""B6: SessionTrust pure-logic — optimistic-demote session trust smoothing."""

from __future__ import annotations

from voice.speaker import SessionTrust


def _trust(**over) -> SessionTrust:
    return SessionTrust(
        accept=over.get("accept", 0.62),
        reject=over.get("reject", 0.45),
        window=over.get("window", 5),
        demote_after=over.get("demote_after", 2),
    )


def test_starts_optimistic_trusted():
    t = _trust()
    assert t.tier == SessionTrust.TRUSTED
    assert t.smoothed is None  # no scores yet


def test_steady_high_stays_trusted():
    t = _trust()
    for _ in range(5):
        assert t.update(0.8) == SessionTrust.TRUSTED


def test_single_low_is_uncertain_not_demoted():
    """One shaky utterance must never lock the owner out (v1's failure mode)."""
    t = _trust(demote_after=2)
    assert t.update(0.2) == SessionTrust.UNCERTAIN


def test_sustained_low_demotes_to_unknown():
    t = _trust(demote_after=2)
    t.update(0.1)  # streak 1 -> uncertain
    assert t.update(0.1) == SessionTrust.UNKNOWN  # streak 2 -> demote


def test_uncertain_band_never_demotes():
    """Scores between reject and accept sit in the uncertain band forever."""
    t = _trust(accept=0.62, reject=0.45)
    for _ in range(6):
        assert t.update(0.55) == SessionTrust.UNCERTAIN


def test_recovers_to_trusted_on_clear_owner():
    t = _trust(window=3, demote_after=2)
    t.update(0.1)
    t.update(0.1)
    assert t.tier == SessionTrust.UNKNOWN
    # a run of clear-owner scores pulls the smoothed mean back above accept
    t.update(0.9)
    t.update(0.9)
    assert t.update(0.9) == SessionTrust.TRUSTED


def test_reset_restores_optimistic_start():
    t = _trust(demote_after=1)
    t.update(0.1)
    assert t.tier == SessionTrust.UNKNOWN
    t.reset()
    assert t.tier == SessionTrust.TRUSTED
    assert t.smoothed is None


def test_smoothing_window_caps_history():
    t = _trust(window=2)
    t.update(0.0)
    t.update(1.0)
    t.update(1.0)
    # only the last two (1.0, 1.0) count
    assert t.smoothed == 1.0


def test_demote_uses_smoothed_not_single_score():
    """A big window absorbs one low score — no demotion on a single dip."""
    t = _trust(window=5, reject=0.45, demote_after=1)
    for _ in range(4):
        t.update(0.8)
    # one low: smoothed = (0.8*4 + 0.1)/5 = 0.66 > reject -> still trusted
    assert t.update(0.1) == SessionTrust.TRUSTED
