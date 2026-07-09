"""B6: speaker_profiles DB round-trip + struct-pack blob fidelity."""

from __future__ import annotations

import pytest

from db.database import Database
from voice.speaker import pack_vector, unpack_vector

pytestmark = pytest.mark.asyncio

CAMPP = "wespeaker_en_voxceleb_CAM++.onnx"
TITANET = "nemo_en_titanet_large.onnx"


async def test_pack_unpack_round_trip():
    vec = [0.1, -0.25, 0.7, 1.0, -1.0]
    out = unpack_vector(pack_vector(vec))
    assert len(out) == len(vec)
    for a, b in zip(vec, out, strict=True):
        assert a == pytest.approx(b, rel=1e-6)


async def test_add_and_read_centroids(tmp_path):
    db = Database(tmp_path / "spk.db")
    await db.connect()
    try:
        await db.add_speaker_centroid("owner", CAMPP, 4, pack_vector([1, 0, 0, 0]), "near")
        await db.add_speaker_centroid("owner", CAMPP, 4, pack_vector([0, 1, 0, 0]), "far")
        rows = await db.speaker_centroids("owner", CAMPP)
        assert len(rows) == 2
        assert {r["kind"] for r in rows} == {"near", "far"}
        assert all(r["dim"] == 4 for r in rows)
        # blobs unpack back to the enrolled vectors
        vecs = sorted(unpack_vector(r["centroid"]) for r in rows)
        assert [0.0, 0.0, 0.0, 1.0] in vecs or [1.0, 0.0, 0.0, 0.0] in vecs
    finally:
        await db.close()


async def test_centroids_are_model_scoped(tmp_path):
    db = Database(tmp_path / "spk.db")
    await db.connect()
    try:
        await db.add_speaker_centroid("owner", CAMPP, 4, pack_vector([1, 0, 0, 0]), "n")
        await db.add_speaker_centroid("owner", TITANET, 3, pack_vector([1, 0, 0]), "n")
        assert len(await db.speaker_centroids("owner", CAMPP)) == 1
        assert len(await db.speaker_centroids("owner", TITANET)) == 1
        assert await db.speaker_centroids("owner", "missing.onnx") == []
    finally:
        await db.close()


async def test_clear_profile_model_scoped_then_all(tmp_path):
    db = Database(tmp_path / "spk.db")
    await db.connect()
    try:
        await db.add_speaker_centroid("owner", CAMPP, 4, pack_vector([1, 0, 0, 0]), "n")
        await db.add_speaker_centroid("owner", TITANET, 3, pack_vector([1, 0, 0]), "n")
        await db.add_speaker_centroid("guest", CAMPP, 4, pack_vector([0, 1, 0, 0]), "n")

        await db.clear_speaker_profile("owner", CAMPP)
        assert await db.speaker_centroids("owner", CAMPP) == []
        assert len(await db.speaker_centroids("owner", TITANET)) == 1  # other model kept
        assert len(await db.speaker_centroids("guest", CAMPP)) == 1  # other label kept

        await db.clear_speaker_profile("owner")  # every model for the label
        assert await db.speaker_centroids("owner", TITANET) == []
        assert len(await db.speaker_centroids("guest", CAMPP)) == 1
    finally:
        await db.close()
