"""Tests for spatial convolver filter-chain rendering."""

from pathlib import Path

import pytest

from eqspace.core.filterchain.spatial import (
    LAYOUT_CHANNEL_SPEAKER_MAP,
    LAYOUT_CHANNELS,
    SpatialChainRenderer,
    SpeakerIR,
    render_from_ir_paths,
)


def _speakers(tmp_path, azimuths=(-30.0, 0.0, 30.0)):
    speakers = []
    channels = ("FL", "FC", "FR")
    for i, az in enumerate(azimuths):
        left = tmp_path / f"ir_{az}_L.wav"
        right = tmp_path / f"ir_{az}_R.wav"
        left.write_bytes(b"")
        right.write_bytes(b"")
        speakers.append(
            SpeakerIR(
                azimuth=az,
                left_ir=left,
                right_ir=right,
                channel=channels[i] if i < len(channels) else None,
            )
        )
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
    assert 'media.class = "Audio/Sink"' in conf
    assert "node.passive = true" in conf


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
        [SpeakerIR(azimuth=0.0, left_ir=left, right_ir=right, channel="FC")]
    )
    assert 'with\\"quote' in conf


def test_render_from_ir_paths_mapping(tmp_path):
    left = tmp_path / "l.wav"
    right = tmp_path / "r.wav"
    left.write_bytes(b"")
    right.write_bytes(b"")
    conf = render_from_ir_paths({30.0: (left, right), -30.0: (left, right)})
    assert conf.count("label = convolver") == 4
    assert 'input  = [ "eqspace.spatial:playback_FL" ]' in conf
    assert 'input  = [ "eqspace.spatial:playback_FR" ]' in conf


def test_custom_channels(tmp_path):
    conf = SpatialChainRenderer().render_config(
        _speakers(tmp_path, azimuths=(0.0,)), channels=("FL", "FR", "LFE")
    )
    assert "audio.position = [ FL FR LFE ]" in conf


def test_render_args_single_line(tmp_path):
    speakers = _speakers(tmp_path)
    args = SpatialChainRenderer().render_args(speakers, gain=2.0)
    assert "\n" not in args
    assert "label = convolver" in args
    assert "label = mixer" in args
    assert '"conv_FL_L"' in args
    assert '"conv_FL_R"' in args
    assert '"conv_FR_L"' in args
    assert '"conv_FR_R"' in args
    for spk in speakers:
        assert f'filename = "{spk.left_ir}"' in args
    assert 'media.class = "Audio/Sink"' in args
    assert "node.passive = true" in args


def test_render_args_matches_config_nodes(tmp_path):
    speakers = _speakers(tmp_path)
    renderer = SpatialChainRenderer()
    conf = renderer.render_config(speakers)
    args = renderer.render_args(speakers)
    for key in (
        "label = convolver",
        "label = mixer",
        "conv_FL_L",
        'node.name = "eqspace.spatial"',
        'node.name = "eqspace.spatial.playback"',
        'media.class = "Audio/Sink"',
    ):
        assert key in conf
        assert key in args


def test_render_args_rejects_bad_input(tmp_path):
    renderer = SpatialChainRenderer()
    with pytest.raises(ValueError, match="at least one"):
        renderer.render_args([])
    with pytest.raises(ValueError, match="gain"):
        renderer.render_args(_speakers(tmp_path), gain=0.0)


def test_layout_channel_speaker_map_stereo(tmp_path):
    renderer = SpatialChainRenderer()
    mapping = LAYOUT_CHANNEL_SPEAKER_MAP["Stereo"]
    speakers = []
    for ch, az in mapping:
        l = tmp_path / f"ir_{ch}_L.wav"
        r = tmp_path / f"ir_{ch}_R.wav"
        l.write_bytes(b"")
        r.write_bytes(b"")
        speakers.append(SpeakerIR(azimuth=az, left_ir=l, right_ir=r, channel=ch))

    conf = renderer.render_config(speakers, channels=LAYOUT_CHANNELS["Stereo"])
    assert 'audio.position = [ FL FR ]' in conf
    assert 'input  = [ "eqspace.spatial:playback_FL" ]' in conf
    assert 'input  = [ "eqspace.spatial:playback_FR" ]' in conf
    # Verify stereo convolvers output to mix_L and mix_R
    assert '"eqspace.spatial.conv_FL_L:Out"' in conf
    assert '"eqspace.spatial.conv_FR_R:Out"' in conf


def test_layout_channel_speaker_map_51(tmp_path):
    renderer = SpatialChainRenderer()
    mapping = LAYOUT_CHANNEL_SPEAKER_MAP["5.1"]
    speakers = []
    for ch, az in mapping:
        l = tmp_path / f"ir_51_{ch}_L.wav"
        r = tmp_path / f"ir_51_{ch}_R.wav"
        l.write_bytes(b"")
        r.write_bytes(b"")
        speakers.append(SpeakerIR(azimuth=az, left_ir=l, right_ir=r, channel=ch))

    channels = LAYOUT_CHANNELS["5.1"]
    conf = renderer.render_config(speakers, channels=channels)
    assert 'audio.position = [ FL FR FC LFE SL SR ]' in conf
    for ch in ("FL", "FR", "FC", "LFE", "SL", "SR"):
        assert f'input  = [ "eqspace.spatial:playback_{ch}" ]' in conf
        assert f'"conv_{ch}_L"' in conf
        assert f'"conv_{ch}_R"' in conf
        assert f'"eqspace.spatial.conv_{ch}_L:Out"' in conf
        assert f'"eqspace.spatial.conv_{ch}_R:Out"' in conf


def test_layout_channel_speaker_map_71(tmp_path):
    renderer = SpatialChainRenderer()
    mapping = LAYOUT_CHANNEL_SPEAKER_MAP["7.1"]
    speakers = []
    for ch, az in mapping:
        l = tmp_path / f"ir_71_{ch}_L.wav"
        r = tmp_path / f"ir_71_{ch}_R.wav"
        l.write_bytes(b"")
        r.write_bytes(b"")
        speakers.append(SpeakerIR(azimuth=az, left_ir=l, right_ir=r, channel=ch))

    channels = LAYOUT_CHANNELS["7.1"]
    conf = renderer.render_config(speakers, channels=channels)
    assert 'audio.position = [ FL FR FC LFE SL SR RL RR ]' in conf
    for ch in ("FL", "FR", "FC", "LFE", "SL", "SR", "RL", "RR"):
        assert f'input  = [ "eqspace.spatial:playback_{ch}" ]' in conf
        assert f'"conv_{ch}_L"' in conf
        assert f'"conv_{ch}_R"' in conf
        assert f'"eqspace.spatial.conv_{ch}_L:Out"' in conf
        assert f'"eqspace.spatial.conv_{ch}_R:Out"' in conf
