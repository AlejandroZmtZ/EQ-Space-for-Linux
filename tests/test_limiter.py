from __future__ import annotations

from pathlib import Path

from eqspace.core.filterchain.limiter import (LimiterCapability, detect_limiter, detect_limiter_with_reason,
                                              render_limiter_args)


def test_missing_plugin_disables_gui_control(monkeypatch):
    from PySide6.QtWidgets import QApplication
    from eqspace.core.pipewire.registry import PwSnapshot
    from eqspace.ui import main_window

    class Registry:
        def snapshot(self):
            return PwSnapshot()

    monkeypatch.setattr(main_window, "detect_limiter_with_reason",
                        lambda: (None, "Install LSP Limiter Stereo LV2"))
    app = QApplication.instance() or QApplication([])
    window = main_window.MainWindow(registry=Registry(), poll_interval_ms=0,
                                    restore_profile=False)
    assert not window.peq.limiter_check.isEnabled()
    assert "Install LSP" in window.peq.limiter_check.toolTip()
    assert "Install LSP" in window.peq.limiter_latency_label.text()
    window.close_completely()


def test_limiter_control_activates_after_eq_route_is_enabled(monkeypatch, tmp_path):
    from PySide6.QtWidgets import QApplication
    from eqspace.core.pipewire.registry import PwSnapshot
    from eqspace.ui import main_window

    class Registry:
        def snapshot(self):
            return PwSnapshot()

    capability = LimiterCapability(tmp_path / "limiter_stereo.ttl", 21,
                                   ("in_l", "in_r"), ("out_l", "out_r"), "out_latency")
    monkeypatch.setattr(main_window, "detect_limiter_with_reason", lambda: (capability, None))
    monkeypatch.setattr(main_window, "lv2_host_directory", lambda: tmp_path)
    app = QApplication.instance() or QApplication([])
    window = main_window.MainWindow(registry=Registry(), poll_interval_ms=0,
                                    restore_profile=False)
    assert not window.peq.limiter_check.isEnabled()
    window.audio_graph.eq_enabled = True
    window._on_routing_changed(True)
    assert window.peq.limiter_check.isEnabled()
    window.audio_graph.eq_enabled = False
    window.close_completely()


def test_window_quit_stays_open_when_audio_handoff_fails(monkeypatch):
    from PySide6.QtWidgets import QApplication
    from eqspace.core.pipewire.registry import PwSnapshot
    from eqspace.ui import main_window

    class Registry:
        def snapshot(self):
            return PwSnapshot()

    app = QApplication.instance() or QApplication([])
    window = main_window.MainWindow(registry=Registry(), poll_interval_ms=0,
                                    restore_profile=False)
    monkeypatch.setattr(type(window.audio_graph), "has_owned_modules", property(lambda self: True))

    def fail_shutdown():
        raise RuntimeError("stream links are unverified")

    monkeypatch.setattr(window.audio_graph, "shutdown", fail_shutdown)
    window.show()

    window.close_completely()

    assert window.isVisible()
    assert "stream links are unverified" in window.peq.status_label.text()
    monkeypatch.undo()
    window.close_completely()


def test_missing_limiter_is_unavailable(tmp_path):
    assert detect_limiter((tmp_path,)) is None


def test_capability_requires_real_ports_and_true_peak_mode(tmp_path):
    plugin = tmp_path / "lsp.lv2"
    plugin.mkdir()
    ttl = plugin / "limiter_stereo.ttl"
    controls = "".join(f'[ a lv2:InputPort, lv2:ControlPort ; lv2:symbol "{name}" ; ]'
                       for name in ("th", "boost", "alr", "g_in", "g_out", "lk"))
    audio = "".join(f'[ a lv2:{direction}Port, lv2:AudioPort ; lv2:symbol "{name}" ; ]'
                    for direction, name in (("Input", "in_l"), ("Input", "in_r"),
                                            ("Output", "out_l"), ("Output", "out_r")))
    latency = '[ a lv2:OutputPort, lv2:ControlPort ; lv2:symbol "latency" ; lv2:portProperty pp:reportsLatency ; ]'
    ovs = ('[ a lv2:InputPort, lv2:ControlPort ; lv2:symbol "ovs" ; '
           'lv2:scalePoint [ rdfs:label "True Peak/32 bit" ; rdf:value 21 ] ; ]')
    ttl.write_text(controls + audio + latency + ovs)
    capability = detect_limiter((tmp_path,))
    assert capability is not None
    assert capability.true_peak_value == 21
    args = render_limiter_args(capability, "eqspace.limiter.test")
    assert '"ovs" = 21' in args
    assert '"boost" = 0' in args and '"alr" = 0' in args
    assert '"th" = 0.891250938' in args
    assert '"limiter:in_l"' in args and '"limiter:out_r"' in args
    ttl.write_text(ttl.read_text().replace("True Peak/32 bit", "Full x8"))
    assert detect_limiter((tmp_path,)) is None
    capability, reason = detect_limiter_with_reason((tmp_path,))
    assert capability is None
    assert "lacks the required true-peak mode" in reason


def test_installed_lsp_build_without_true_peak_is_reported(tmp_path):
    plugin = tmp_path / "lsp.lv2"
    plugin.mkdir()
    controls = "".join(f'[ a lv2:InputPort, lv2:ControlPort ; lv2:symbol "{name}" ; ]'
                       for name in ("ovs", "th", "boost", "alr", "g_in", "g_out", "lk"))
    audio = ''.join(f'[ a lv2:{direction}Port, lv2:AudioPort ; lv2:symbol "{name}" ; ]'
                    for direction, name in (("Input", "in_l"), ("Input", "in_r"),
                                            ("Output", "out_l"), ("Output", "out_r")))
    latency = '[ a lv2:OutputPort, lv2:ControlPort ; lv2:symbol "out_latency" ; lv2:portProperty pp:reportsLatency ; ]'
    (plugin / "limiter_stereo.ttl").write_text(controls + audio + latency)
    capability, reason = detect_limiter_with_reason((tmp_path,))
    assert capability is None
    assert "lacks the required true-peak mode" in reason


def test_missing_lv2_host_fails_before_spawning(monkeypatch):
    import pytest
    from eqspace.core.filterchain import limiter
    from eqspace.core.filterchain.manager import FilterChainError
    from eqspace.ui.module_args_manager import ModuleArgsManager
    monkeypatch.setattr(limiter, 'lv2_host_directory', lambda: None)
    def unexpected(*args, **kwargs):
        raise AssertionError('must not spawn an unsupported host')
    manager = ModuleArgsManager(popen=unexpected)
    with pytest.raises(FilterChainError, match='LV2 host is missing'):
        manager.load_args('filter.graph = { nodes = [ { type = lv2 } ] }')


def test_manual_apply_preserves_limiter(monkeypatch):
    from PySide6.QtWidgets import QApplication
    from eqspace.core.pipewire.registry import PwSnapshot
    from eqspace.ui.main_window import MainWindow
    class Registry:
        def snapshot(self):
            return PwSnapshot()
    app = QApplication.instance() or QApplication([])
    window = MainWindow(registry=Registry(), poll_interval_ms=0, restore_profile=False)
    captured = []
    monkeypatch.setattr(window, 'apply_profile', lambda profile, **kwargs: captured.append(profile))
    window.audio_graph.limiter_name = 'eqspace.limiter.active'
    window._on_manual_peq_apply()
    assert captured[-1].limiter_enabled
    from eqspace.core.profiles.models import EQProfile
    window._on_preset_apply_requested(EQProfile.from_bands('Flat', []))
    assert captured[-1].limiter_enabled
    window.audio_graph.limiter_name = None
    window.close_completely()


def test_host_selection_uses_matching_pipewire_version(monkeypatch, tmp_path):
    import subprocess
    from eqspace.core.filterchain import limiter
    monkeypatch.delenv('PIPEWIRE_MODULE_DIR', raising=False)
    monkeypatch.setattr(Path, 'home', lambda: tmp_path)
    monkeypatch.setattr(subprocess, 'run', lambda *args, **kwargs:
                        subprocess.CompletedProcess(args[0], 0, 'pw-cli\nCompiled with libpipewire 1.0.5\nLinked with libpipewire 1.0.5\n', ''))
    root = tmp_path / '.local/lib/eqspace/pipewire-1.0.5'
    root.mkdir(parents=True)
    for name in ('libpipewire-module-filter-chain.so', 'libpipewire-module-filter-chain-lv2.so'):
        (root / name).touch()
    # Isolate distro paths so this test also works on hosts shipping LV2 support.
    original_is_file = Path.is_file
    monkeypatch.setattr(Path, 'is_file', lambda path: original_is_file(path) if path.is_relative_to(tmp_path) else False)
    assert limiter.lv2_host_directory() == root
    root.rename(root.with_name('pipewire-1.2.7'))
    assert limiter.lv2_host_directory() is None


def test_missing_host_disables_control_even_with_plugin(monkeypatch, tmp_path):
    from PySide6.QtWidgets import QApplication
    from eqspace.core.pipewire.registry import PwSnapshot
    from eqspace.ui import main_window
    class Registry:
        def snapshot(self):
            return PwSnapshot()
    capability = LimiterCapability(tmp_path / 'limiter_stereo.ttl', 21,
                                   ('in_l', 'in_r'), ('out_l', 'out_r'), 'out_latency')
    monkeypatch.setattr(main_window, 'detect_limiter_with_reason', lambda: (capability, None))
    monkeypatch.setattr(main_window, 'lv2_host_directory', lambda: None)
    app = QApplication.instance() or QApplication([])
    window = main_window.MainWindow(registry=Registry(), poll_interval_ms=0, restore_profile=False)
    window.audio_graph.eq_enabled = True
    window._sync_limiter_ui()
    assert not window.peq.limiter_check.isEnabled()
    assert 'LV2 host is missing' in window.peq.limiter_check.toolTip()
    window.audio_graph.eq_enabled = False
    window.close_completely()


def test_disabling_limiter_reserves_headroom_before_removing_stage(monkeypatch):
    from PySide6.QtWidgets import QApplication
    from eqspace.core.pipewire.registry import PwSnapshot
    from eqspace.ui.main_window import MainWindow
    class Registry:
        def snapshot(self):
            return PwSnapshot()
    app = QApplication.instance() or QApplication([])
    window = MainWindow(registry=Registry(), poll_interval_ms=0, restore_profile=False)
    events = []
    monkeypatch.setattr(window, '_on_spatial_changed', lambda peak, **kwargs:
                        events.append(('headroom', kwargs.get('limiter_enabled'))))
    monkeypatch.setattr(window.audio_graph, 'set_limiter', lambda enabled, cap:
                        events.append(('limiter', enabled)))
    window._change_limiter(False)
    assert events == [('headroom', False), ('limiter', False), ('headroom', None)]
    window.close_completely()
