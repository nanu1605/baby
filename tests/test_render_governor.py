"""V2 frame governor — backend additive surface: VRAM quantization for the
/ws/state diff, and the code-defaulted render.* config read. Pure, no server."""

from __future__ import annotations

from ui.server import _DEFAULT_RENDER, _quantize_vram, _render_config, _ui_brain


def test_quantize_buckets_to_quarter_gb():
    assert _quantize_vram(1.0) == 1.0
    assert _quantize_vram(7.58) == 7.5   # 30.32 -> 30 -> 7.5
    assert _quantize_vram(7.62) == 7.5   # 30.48 -> 30 -> 7.5
    assert _quantize_vram(7.63) == 7.75  # 30.52 -> 31 -> 7.75


def test_quantize_keeps_the_diff_quiet_within_a_bucket():
    # Small wiggles land in the same bucket -> identical value -> no /ws/state send.
    # (3.01 and 3.12 both fall in the [3.0, 3.125) bucket -> 3.0.)
    assert _quantize_vram(3.01) == _quantize_vram(3.12) == 3.0
    # Crossing a bucket boundary (the 9B loading) changes the value -> a send fires.
    assert _quantize_vram(1.0) != _quantize_vram(7.6)


def test_render_config_defaults_when_absent():
    assert _render_config({}) == _DEFAULT_RENDER
    assert _render_config({"render": None}) == _DEFAULT_RENDER
    assert _render_config({"render": "nonsense"}) == _DEFAULT_RENDER


def test_render_config_reads_overrides():
    cfg = {"render": {"target_fps": 30, "tier": "lite3d", "idle_full_on_desktop": False}}
    got = _render_config(cfg)
    assert got == {"target_fps": 30, "tier": "lite3d", "idle_full_on_desktop": False}


def test_render_config_coerces_types():
    cfg = {"render": {"target_fps": "45", "idle_full_on_desktop": 1}}
    got = _render_config(cfg)
    assert got["target_fps"] == 45 and got["idle_full_on_desktop"] is True
    assert got["tier"] == "auto"  # untouched default


def test_render_config_degrades_on_garbage_instead_of_raising():
    # A typo'd target_fps must fall back to the default, never 500 /stats.
    assert _render_config({"render": {"target_fps": "60fps"}})["target_fps"] == 60
    assert _render_config({"render": {"target_fps": None}})["target_fps"] == 60


def test_ui_brain_defaults_to_3d():
    assert _ui_brain({}) == "3d"
    assert _ui_brain({"ui": {}}) == "3d"
    assert _ui_brain({"ui": {"brain": "3d"}}) == "3d"


def test_ui_brain_2d_is_the_rollback():
    assert _ui_brain({"ui": {"brain": "2d"}}) == "2d"
    assert _ui_brain({"ui": {"brain": " 2D "}}) == "2d"


def test_ui_brain_unknown_value_falls_back_to_3d():
    assert _ui_brain({"ui": {"brain": "wat"}}) == "3d"
