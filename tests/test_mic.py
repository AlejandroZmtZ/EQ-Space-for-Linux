"""Tests for the DeepFilterNet mic noise-reduction filter-chain."""

import pytest

from eqspace.core.filterchain.mic import (
    DEEPFILTERNET_LABEL,
    LADSPA_NODE_TYPE,
    LADSPA_PLUGIN_FILE,
    LADSPA_SEARCH_PATHS,
    MicChainRenderer,
    deepfilternet_available,
    find_deepfilternet,
    render_mic_args,
    render_mic_config,
)


@pytest.fixture
def fake_plugin(tmp_path):
    plugin_dir = tmp_path / "ladspa"
    plugin_dir.mkdir()
    plugin = plugin_dir / LADSPA_PLUGIN_FILE
    plugin.write_bytes(b"")
    return plugin


def test_find_deepfilternet(fake_plugin):
    assert find_deepfilternet([str(fake_plugin.parent)]) == fake_plugin


def test_find_deepfilternet_missing(tmp_path):
    assert find_deepfilternet([str(tmp_path)]) is None


def test_deepfilternet_available(fake_plugin, tmp_path):
    assert deepfilternet_available([str(fake_plugin.parent)])
    assert not deepfilternet_available([str(tmp_path)])


def test_search_paths_cover_standard_dirs():
    assert "/usr/lib/ladspa" in LADSPA_SEARCH_PATHS
    assert "/usr/local/lib/ladspa" in LADSPA_SEARCH_PATHS
    assert any(p.endswith("/.ladspa") for p in LADSPA_SEARCH_PATHS)
    assert "/usr/lib/x86_64-linux-gnu/ladspa" in LADSPA_SEARCH_PATHS


def test_renderer_raises_without_plugin(tmp_path):
    import eqspace.core.filterchain.mic as mic

    original = mic.LADSPA_SEARCH_PATHS
    try:
        mic.LADSPA_SEARCH_PATHS = (str(tmp_path),)
        with pytest.raises(FileNotFoundError, match="libdf_ladspa"):
            MicChainRenderer()
    finally:
        mic.LADSPA_SEARCH_PATHS = original


def test_render_contains_ladspa_node(fake_plugin):
    conf = MicChainRenderer(plugin_path=fake_plugin).render_config()
    assert "libpipewire-module-filter-chain" in conf
    assert f"type  = {LADSPA_NODE_TYPE}" in conf
    assert f'plugin = "{fake_plugin}"' in conf
    assert f"label = {DEEPFILTERNET_LABEL}" in conf


def test_render_args_single_line(fake_plugin):
    args = MicChainRenderer(plugin_path=fake_plugin).render_args(
        attenuation_db=-20.0, strength=0.5
    )
    assert "\n" not in args
    assert f"type = {LADSPA_NODE_TYPE}" in args
    assert f"label = {DEEPFILTERNET_LABEL}" in args
    assert f'plugin = "{fake_plugin}"' in args
    assert '"Attenuation Limit (dB)" = -20.0' in args
    assert '"Strength" = 0.5' in args
    assert 'media.class = "Audio/Source/Virtual"' in args
    assert 'node.name = "eqspace.mic"' in args


def test_render_args_matches_config_nodes(fake_plugin):
    renderer = MicChainRenderer(plugin_path=fake_plugin)
    conf = renderer.render_config()
    args = renderer.render_args()
    for key in (
        '"df_noise_reduction"',
        str(fake_plugin),
        DEEPFILTERNET_LABEL,
        '"Attenuation Limit (dB)" = -15.0',
        '"Strength" = 1.0',
    ):
        assert key in conf
        assert key in args


def test_render_args_validates_params(fake_plugin):
    renderer = MicChainRenderer(plugin_path=fake_plugin)
    with pytest.raises(ValueError, match="attenuation"):
        renderer.render_args(attenuation_db=5.0)
    with pytest.raises(ValueError, match="strength"):
        renderer.render_args(strength=1.5)


def test_render_mic_args_wrapper(fake_plugin):
    args = render_mic_args(plugin_path=fake_plugin)
    assert "\n" not in args
    assert f"label = {DEEPFILTERNET_LABEL}" in args


def test_render_virtual_source(fake_plugin):
    conf = MicChainRenderer(plugin_path=fake_plugin).render_config()
    assert 'media.class = "Audio/Source/Virtual"' in conf
    assert 'node.description = "EQ-Space Mic"' in conf
    assert 'node.name = "eqspace.mic"' in conf


def test_render_controls(fake_plugin):
    conf = render_mic_config(
        attenuation_db=-20.0, strength=0.5, plugin_path=fake_plugin
    )
    assert '"Attenuation Limit (dB)" = -20.0' in conf
    assert '"Strength" = 0.5' in conf


def test_render_defaults(fake_plugin):
    conf = render_mic_config(plugin_path=fake_plugin)
    assert '"Attenuation Limit (dB)" = -15.0' in conf
    assert '"Strength" = 1.0' in conf


def test_render_validates_params(fake_plugin):
    renderer = MicChainRenderer(plugin_path=fake_plugin)
    with pytest.raises(ValueError, match="attenuation"):
        renderer.render_config(attenuation_db=5.0)
    with pytest.raises(ValueError, match="strength"):
        renderer.render_config(strength=1.5)


def test_custom_source_name(fake_plugin):
    conf = MicChainRenderer(
        plugin_path=fake_plugin, source_name="My Mic"
    ).render_config()
    assert 'node.description = "My Mic"' in conf
