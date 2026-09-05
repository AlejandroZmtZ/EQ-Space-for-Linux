"""Tests for spatial convolver filter-chain rendering."""

from pathlib import Path

import pytest

from eqspace.core.filterchain.spatial import (
    SpeakerIR,
    SpatialChainRenderer,
    render_from_ir_paths,
)


def _speakers(tmp_path, azimuths=(-30.0, 0.0, 30.0)):
    speakers = []
    for az in azimuths:
        left = tmp_path / f"ir_{az}_L.wav"
        right = tmp_path / f"ir_{az}_R.wav"
        left.write_bytes(b"")
        right.write_bytes(b"")
        speakers.append(SpeakerIR(azimuth=az, left_ir=left, right_ir=right))
    return speakers


def test_render_contains_convolver_nodes(tmp_path):
    conf = SpatialChainRenderer().render_config(_speakers(tmp_path))
    assert "libpipewire-module-filter-chain" in conf
    assert conf.count("label = convolver") == 6  # 3 speakers x 2 ears
    assert conf.count("label = mixer") == 2  # one per ear


def test_render_embeds_ir_paths(tmp_path):
    speakers = _speakers(tmp_path)
    conf = SpatialChainRenderer().render_config(speakers)
    for spk in speakers:
        assert f'"filename" = "{spk.left_ir}"' in conf
        assert f'"filename" = "{spk.right_ir}"' in conf


def test_render_names_and_positions(tmp_path):
    conf = SpatialChainRenderer().render_config(_speakers(tmp_path))
    assert 'node.name = "eqspace.spatial"' in conf
    assert 'node.description = "EQ-Space Spatial"' in conf
    assert "audio.position = [ FL FR ]" in conf
    assert 'media.class = "Stream/Filter"' in conf


def test_render_empty_speakers_rejected(tmp_path):
    with pytest.raises(ValueError, match="at least one"):
        SpatialChainRenderer().render_config([])


def test_render_bad_gain_rejected(tmp_path):
    with pytest.raises(ValueError, match="gain"):
        SpatialChainRenderer().render_config(_speakers(tmp_path), gain=0.0)


def test_render_escapes_paths(tmp_path):
    weird = tmp_path / 'with"quote'
    weird.mkdir()
    left = weird / "l.wav"
    right = weird / "r.wav"
    left.write_bytes(b"")
    right.write_bytes(b"")
    conf = SpatialChainRenderer().render_config(
        [SpeakerIR(azimuth=0.0, left_ir=left, right_ir=right)]
    )
    assert 'with\\"quote' in conf


def test_render_from_ir_paths_mapping(tmp_path):
    left = tmp_path / "l.wav"
    right = tmp_path / "r.wav"
    left.write_bytes(b"")
    right.write_bytes(b"")
    conf = render_from_ir_paths({30.0: (left, right), -30.0: (left, right)})
    assert conf.count("label = convolver") == 4


def test_custom_channels(tmp_path):
    conf = SpatialChainRenderer().render_config(
        _speakers(tmp_path, azimuths=(0.0,)), channels=("FL", "FR", "LFE")
    )
    assert "audio.position = [ FL FR LFE ]" in conf
