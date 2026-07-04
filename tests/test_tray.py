"""TrayState: pure bus-event → status folding (no pystray import needed)."""

from __future__ import annotations

from ui.tray import TrayState


def test_initial_status_is_ready():
    assert TrayState().status() == "ready"


def test_turn_makes_busy_then_ready_again():
    state = TrayState()
    assert state.apply("turn_start") == "busy"
    assert state.apply("turn_end") == "ready"


def test_background_task_makes_busy():
    state = TrayState()
    assert state.apply("task_started") == "busy"
    assert state.apply("task_done") == "ready"


def test_confirm_wins_over_busy():
    state = TrayState()
    state.apply("turn_start")
    assert state.apply("confirm_request") == "confirm"
    assert state.apply("confirm_resolved") == "busy"  # turn still running
    assert state.apply("turn_end") == "ready"


def test_overlapping_turn_and_task():
    state = TrayState()
    state.apply("turn_start")
    state.apply("task_started")
    assert state.apply("turn_end") == "busy"  # task still running
    assert state.apply("task_done") == "ready"


def test_counters_never_go_negative():
    state = TrayState()
    state.apply("turn_end")
    state.apply("task_done")
    state.apply("confirm_resolved")
    assert state.status() == "ready"
    assert state.apply("turn_start") == "busy"


def test_unknown_kinds_ignored():
    state = TrayState()
    assert state.apply("token") == "ready"
    assert state.apply("status") == "ready"
