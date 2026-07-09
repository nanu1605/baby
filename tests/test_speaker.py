"""Phase 5 stage 5: speaker verification core (offline — fake extractor)."""

from __future__ import annotations

import json

import numpy as np
import pytest

from voice.speaker import SpeakerVerifier, cosine

pytestmark = pytest.mark.asyncio


class FakeExtractor:
    def __init__(self, embedding, dim=4):
        self.embedding = list(embedding)
        self.dim = dim
        self.seen = []

    def embed(self, pcm16):
        self.seen.append(pcm16)
        return list(self.embedding)


def write_profile(path, mean, dim=4, embeddings=None):
    path.write_text(
        json.dumps(
            {
                "model": "test.onnx",
                "dim": dim,
                "mean": list(mean),
                "embeddings": embeddings or [list(mean)],
            }
        ),
        encoding="utf-8",
    )


def make_verifier(tmp_path, *, mean=(1, 0, 0, 0), embedding=(1, 0, 0, 0), **over):
    profile = tmp_path / "owner_voice.json"
    write_profile(profile, mean, dim=over.pop("dim", 4))
    verifier = SpeakerVerifier(
        model_path=tmp_path / "model.onnx",
        profile_path=profile,
        threshold=over.pop("threshold", 0.5),
        extractor=FakeExtractor(embedding, dim=over.pop("extractor_dim", 4)),
    )
    return verifier


# -- cosine (pure) --------------------------------------------------------------------


async def test_cosine_identity_orthogonal_and_zero():
    assert cosine([1, 0], [1, 0]) == pytest.approx(1.0)
    assert cosine([1, 0], [0, 1]) == pytest.approx(0.0)
    assert cosine([1, 0], [-1, 0]) == pytest.approx(-1.0)
    assert cosine([0, 0], [1, 0]) == 0.0


# -- verify ----------------------------------------------------------------------------


async def test_owner_voice_passes(tmp_path):
    verifier = make_verifier(tmp_path, mean=(1, 0, 0, 0), embedding=(0.9, 0.1, 0, 0))
    assert "on" in verifier.load()
    ok, similarity = verifier.verify(np.zeros(16000, dtype=np.int16))
    assert ok is True and similarity > 0.9


async def test_stranger_voice_fails(tmp_path):
    verifier = make_verifier(tmp_path, mean=(1, 0, 0, 0), embedding=(0, 1, 0, 0))
    verifier.load()
    ok, similarity = verifier.verify(np.zeros(16000, dtype=np.int16))
    assert ok is False and similarity < 0.1


async def test_threshold_boundary(tmp_path):
    verifier = make_verifier(
        tmp_path, mean=(1, 0, 0, 0), embedding=(1, 0, 0, 0), threshold=1.0
    )
    verifier.load()
    ok, similarity = verifier.verify(np.zeros(100, dtype=np.int16))
    assert similarity == pytest.approx(1.0)
    assert ok is True  # >= threshold


# -- fail-soft load paths ---------------------------------------------------------------


async def test_missing_profile_disables(tmp_path):
    verifier = SpeakerVerifier(
        model_path=tmp_path / "model.onnx",
        profile_path=tmp_path / "nope.json",
        extractor=FakeExtractor((1, 0, 0, 0)),
    )
    note = verifier.load()
    assert verifier.enabled is False
    assert "no enrollment" in note


async def test_bad_profile_json_disables(tmp_path):
    profile = tmp_path / "owner_voice.json"
    profile.write_text("{ not json", encoding="utf-8")
    verifier = SpeakerVerifier(
        profile_path=profile, extractor=FakeExtractor((1, 0, 0, 0))
    )
    assert "bad profile" in verifier.load()
    assert verifier.enabled is False


async def test_missing_model_file_disables(tmp_path):
    profile = tmp_path / "owner_voice.json"
    write_profile(profile, (1, 0, 0, 0))
    verifier = SpeakerVerifier(
        model_path=tmp_path / "missing.onnx", profile_path=profile, extractor=None
    )
    note = verifier.load()
    assert verifier.enabled is False
    assert "model file missing" in note


async def test_dim_mismatch_disables(tmp_path):
    verifier = make_verifier(tmp_path, dim=4, extractor_dim=192)
    note = verifier.load()
    assert verifier.enabled is False
    assert "dimension mismatch" in note


async def test_profile_round_trip_matches_enrollment_shape(tmp_path):
    """The verifier accepts exactly what enroll_voice.py writes."""
    embeddings = [[0.9, 0.1, 0.0, 0.0], [1.0, 0.0, 0.0, 0.0]]
    mean = list(np.mean(np.asarray(embeddings, dtype=np.float32), axis=0))
    profile = tmp_path / "owner_voice.json"
    profile.write_text(
        json.dumps(
            {
                "model": "wespeaker_en_voxceleb_CAM++.onnx",
                "dim": 4,
                "created_at": "2026-07-05T00:00:00+00:00",
                "embeddings": embeddings,
                "mean": [float(x) for x in mean],
            }
        ),
        encoding="utf-8",
    )
    verifier = SpeakerVerifier(
        profile_path=profile, extractor=FakeExtractor((0.95, 0.05, 0, 0))
    )
    assert "on" in verifier.load()
    ok, _ = verifier.verify(np.zeros(100, dtype=np.int16))
    assert ok is True


# -- v2 multi-centroid (B6) ------------------------------------------------------------


async def test_db_centroids_score_by_max_cosine(tmp_path):
    """A profile is a SET of centroids; verify keeps the best match."""
    verifier = SpeakerVerifier(
        model_path=tmp_path / "model.onnx",
        extractor=FakeExtractor((0.9, 0.1, 0, 0), dim=4),
        centroids=[[1, 0, 0, 0], [0, 0, 1, 0]],  # near-match + orthogonal
    )
    note = verifier.load()
    assert "2 centroids" in note
    ok, similarity = verifier.verify(np.zeros(100, dtype=np.int16))
    assert ok is True
    assert similarity > 0.99  # max over centroids, not the orthogonal one


async def test_db_centroids_take_precedence_over_json(tmp_path):
    """When both exist, DB centroids win over the v1 JSON mean."""
    profile = tmp_path / "owner_voice.json"
    write_profile(profile, (0, 1, 0, 0))  # JSON mean is orthogonal to the utterance
    verifier = SpeakerVerifier(
        profile_path=profile,
        extractor=FakeExtractor((1, 0, 0, 0), dim=4),
        centroids=[[1, 0, 0, 0]],  # DB centroid aligns with the utterance
    )
    assert "centroids" in verifier.load()
    ok, similarity = verifier.verify(np.zeros(100, dtype=np.int16))
    assert ok is True and similarity > 0.99  # JSON mean would have scored ~0


async def test_db_centroids_all_dim_mismatch_disables(tmp_path):
    verifier = SpeakerVerifier(
        model_path=tmp_path / "model.onnx",
        extractor=FakeExtractor((1, 0, 0, 0), dim=4),
        centroids=[[1, 0, 0]],  # dim 3 against a dim-4 extractor
    )
    note = verifier.load()
    assert verifier.enabled is False
    assert "dimension mismatch" in note


async def test_db_centroids_partial_dim_match_keeps_good(tmp_path):
    verifier = SpeakerVerifier(
        model_path=tmp_path / "model.onnx",
        extractor=FakeExtractor((1, 0, 0, 0), dim=4),
        centroids=[[1, 0, 0, 0], [1, 0, 0]],  # one valid, one wrong-dim
    )
    note = verifier.load()
    assert verifier.enabled is True
    assert "1 centroids" in note
