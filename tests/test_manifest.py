"""v6 W3: the first-run dependency manifest -- the single source of truth the
orchestrator, wizard checklist, and first-run harness read. These tests pin the
invariants that keep a stranger's install correct: mode gating, required/optional
split, and the disk pre-check budget."""

from __future__ import annotations

import pytest

from core import manifest
from core.manifest import INSTALL_KINDS, MANIFEST


def test_every_dep_is_well_formed():
    keys = [d.key for d in MANIFEST]
    assert len(keys) == len(set(keys)), "duplicate dep keys"
    for d in MANIFEST:
        assert d.install_kind in INSTALL_KINDS, f"{d.key}: bad install_kind {d.install_kind}"
        assert d.mode_gated in (None, "full"), f"{d.key}: bad mode_gated"
        assert d.assets, f"{d.key}: no assets declared"
        for a in d.assets:
            assert a.approx_mb >= 0
            assert isinstance(a.auto_downloads, bool)


def test_full_mode_includes_ollama_cloud_only_excludes_it():
    full_keys = {d.key for d in manifest.deps_for_mode("full")}
    cloud_keys = {d.key for d in manifest.deps_for_mode("cloud_only")}
    # The local brain is Full-only; a cloud-only install must never try to pull it.
    assert {"ollama-daemon", "ollama-model"} <= full_keys
    assert not ({"ollama-daemon", "ollama-model"} & cloud_keys)
    # Everything else (voice, memory, runtime) applies to both modes.
    assert "whisper" in cloud_keys and "kokoro" in cloud_keys and "embedder" in cloud_keys


def test_required_set_matches_spec():
    req_full = {d.key for d in manifest.required_deps("full")}
    # Voice is required OOTB (owner decision); memory + backend too; 9B required in Full.
    assert {"python-backend", "vcredist", "whisper", "kokoro", "wakeword", "embedder"} <= req_full
    assert {"ollama-daemon", "ollama-model"} <= req_full
    # Speaker verify, browser, sensors are optional -- never gate a first-run.
    for opt in ("speaker", "chromium", "sensors"):
        assert not manifest.get(opt).required


def test_cloud_only_required_drops_local_brain():
    req_cloud = {d.key for d in manifest.required_deps("cloud_only")}
    assert "ollama-model" not in req_cloud and "ollama-daemon" not in req_cloud
    assert "whisper" in req_cloud  # voice still required in cloud-only


def test_disk_footprint_full_exceeds_cloud_only():
    # Full carries the ~6.6 GB 9B + the ~700 MB daemon, so it must be much larger.
    full = manifest.disk_footprint_mb("full")
    cloud = manifest.disk_footprint_mb("cloud_only")
    assert full > cloud
    assert full - cloud >= 6600  # at least the 9B weights


def test_explicit_fetches_flag_the_non_free_downloads():
    # kokoro (explicit URL), wakeword (download_models), 9B (ollama_pull) must be in
    # the active-fetch list; the uv_sync backend is NOT (it comes for free with sync).
    fetch_keys = {d.key for d in manifest.explicit_fetches("full")}
    assert {"kokoro", "wakeword", "ollama-model", "vcredist"} <= fetch_keys
    assert "python-backend" not in fetch_keys


def test_kokoro_and_speaker_land_in_models_dir():
    # The path-loaded assets must relocate to models_dir (per-user writable), not the
    # read-mostly install dir -- this is the load-bearing dest for W3a's relocation.
    for key in ("kokoro", "speaker"):
        assert any(a.dest == "models_dir" for a in manifest.get(key).assets), key


def test_auto_download_truth_is_pinned():
    # whisper + e5 auto-download (HF cache); kokoro + 9B + wakeword do NOT.
    assert all(a.auto_downloads for a in manifest.get("whisper").assets)
    assert all(a.auto_downloads for a in manifest.get("embedder").assets)
    assert not any(a.auto_downloads for a in manifest.get("kokoro").assets)
    assert not any(a.auto_downloads for a in manifest.get("ollama-model").assets)
    assert not any(a.auto_downloads for a in manifest.get("wakeword").assets)


def test_vcredist_flagged_admin():
    # The one step that breaks the no-admin promise -- must be marked so the harness
    # can elevate for just it.
    assert manifest.get("vcredist").needs_admin is True


def test_get_unknown_raises():
    with pytest.raises(KeyError):
        manifest.get("does-not-exist")
