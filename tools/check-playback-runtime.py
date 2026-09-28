#!/usr/bin/env python3
"""Exercise real playback modules on a private PipeWire daemon and null sink.

Run with .venv/bin/python tools/check-playback-runtime.py --require-limiter.
This never selects a host sink: all clients inherit a private runtime directory,
remote name, policy state and D-Bus session. Audio comes from /dev/zero into a
null sink; this establishes control/link behavior, not audible or true-peak proof.
"""
from __future__ import annotations

import argparse
import itertools
import json
import logging
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import traceback
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))

PIPEWIRE_CONFIG = '''
context.properties = {
    core.daemon = true
    core.name = eqspace-probe
    default.clock.rate = 48000
    default.clock.quantum = 256
    support.dbus = false
}
context.spa-libs = {
    audio.convert.* = audioconvert/libspa-audioconvert
    support.* = support/libspa-support
}
context.modules = [
    { name = libpipewire-module-protocol-native }
    { name = libpipewire-module-metadata }
    { name = libpipewire-module-spa-device-factory }
    { name = libpipewire-module-spa-node-factory }
    { name = libpipewire-module-client-node }
    { name = libpipewire-module-client-device }
    { name = libpipewire-module-access args = { access.force = unrestricted } }
    { name = libpipewire-module-adapter }
    { name = libpipewire-module-link-factory }
    { name = libpipewire-module-session-manager }
]
context.objects = [
    { factory = spa-node-factory args = {
        factory.name = support.node.driver node.name = Dummy-Driver
        node.group = pipewire.dummy priority.driver = 20000
    } }
    { factory = adapter args = {
        factory.name = support.null-audio-sink
        node.name = test.output node.description = "Private null output"
        media.class = Audio/Sink audio.position = [ FL FR ]
        object.linger = true
    } }
]
'''


def wait_for(predicate, detail, timeout=12):
    deadline = time.monotonic() + timeout
    last = None
    while time.monotonic() < deadline:
        try:
            if predicate():
                return
        except Exception as exc:
            last = exc
        time.sleep(.05)
    raise RuntimeError(f'{detail}: {last or "timed out"}')


def exercise_window(registry, capability, record):
    """Real Qt controls/coordinator backed by the already-running private daemon."""
    from PySide6.QtCore import Qt, QTimer
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication
    from eqspace.core.profiles.models import EQProfile
    from eqspace.core.filterchain.spatial import hybrid_controls, HYBRID_DESCRIPTION
    from eqspace.core.pipewire import control
    from eqspace.ui.main_window import MainWindow
    import math
    import statistics
    import threading

    app = QApplication.instance() or QApplication([])
    app.setQuitOnLastWindowClosed(False)
    window = MainWindow(registry=registry, poll_interval_ms=0, restore_profile=False)
    window.show()
    app.processEvents()
    assert window.audio_graph.atomic_transitions
    heartbeats = []
    heartbeat = QTimer()
    heartbeat.setInterval(10)
    heartbeat.timeout.connect(lambda: heartbeats.append(time.monotonic()))
    heartbeat.start()

    def pump(predicate, description, timeout=20):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            app.processEvents()
            if predicate():
                return
            time.sleep(.002)
        raise RuntimeError(f'GUI wait failed: {description}; EQ={window.peq.status_label.text()}; '
                           f'Spatial={window.spatial.status_label.text()}')

    def idle():
        return (window._graph_worker is None and not window._profile_apply_busy
                and not window._live_eq_pending and not window._live_eq_timer.isActive()
                and window._live_spatial_pending is None and not window._live_spatial_timer.isActive())

    spatial = {'profile': HYBRID_DESCRIPTION, 'wet': 75,
               'layout': 'HoloSpace 3D', 'crossfeed': False, 'stereo_expansion': True}

    def apply(eq_on, spatial_on, limiter_on, *, automatic=True):
        results = []
        profile = EQProfile.from_bands('Private GUI probe', [], scope='playback',
            eq_enabled=eq_on, spatial_enabled=spatial_on, spatial=spatial,
            limiter_enabled=limiter_on, preamp_db=-6, automatic_headroom=automatic)
        before = len(heartbeats)
        started = time.monotonic()
        assert window.apply_profile(profile, async_mode=True,
                                    on_done=lambda success, message: results.append((success, message)))
        pump(lambda: bool(results) and idle(), 'background profile Apply')
        assert results[-1][0], results[-1][1]
        window.audio_graph._observe_path(window.audio_graph._current_stages(), 'test.output')
        assert window.audio_graph.active_stages() == tuple(label for on, label in (
            (spatial_on, 'Spatial'), (eq_on, 'EQ'), (limiter_on, 'LSP Limiter')) if on)
        assert len(heartbeats) > before, 'Qt event loop did not run during Apply'
        return round((time.monotonic() - started) * 1000, 2)

    try:
        pump(lambda: window.mixer.selected_output_name == 'test.output'
             and getattr(window.mixer, '_refresh_worker', None) is None and len(heartbeats) >= 2,
             'initial asynchronous physical-output discovery')
        record('GUI initial output discovery', selected_output=window.mixer.selected_output_name)
        for eq_on, spatial_on, limiter_on in itertools.product((False, True), repeat=3):
            if limiter_on and capability is None:
                continue
            elapsed = apply(eq_on, spatial_on, limiter_on)
            record('GUI background profile Apply', eq=eq_on, spatial=spatial_on,
                   limiter=limiter_on, elapsed_ms=elapsed)
        apply(True, True, capability is not None, automatic=False)
        window.tabs.setCurrentWidget(window.peq)
        spin = window.peq.preamp_spin
        spin.setFocus()
        app.processEvents()
        manager = window.peq.manager
        sink = manager.node_name
        original_reload = manager.reload
        in_flight = 0
        maximum_in_flight = 0
        reload_count = 0
        mutex = threading.Lock()

        def observed_reload(*args, **kwargs):
            nonlocal in_flight, maximum_in_flight, reload_count
            with mutex:
                in_flight += 1
                reload_count += 1
                maximum_in_flight = max(maximum_in_flight, in_flight)
            try:
                return original_reload(*args, **kwargs)
            finally:
                with mutex:
                    in_flight -= 1
        manager.reload = observed_reload

        def applied_preamp(expected):
            return (idle() and window._applied_eq_profile is not None
                    and math.isclose(window._applied_eq_profile.preamp_db, expected, abs_tol=.0001))

        def assert_preamp(expected):
            assert math.isclose(spin.value(), expected, abs_tol=.0001)
            assert window.peq.manager is manager and manager.node_name == sink
            assert window.audio_graph.is_path_verified()
            manager.verify_controls(window.peq._last_good_specs)
            controls = manager._read_controls(2.0)
            assert math.isclose(controls['preamp:Mult'], 10 ** (expected / 20), rel_tol=1e-5)
            assert spin.hasFocus() or spin.lineEdit().hasFocus(), 'live update lost editor focus'

        timings = []
        for index in range(20):
            expected = -5.5 if index % 2 == 0 else -6.0
            started = time.monotonic()
            QTest.keyClick(spin, Qt.Key.Key_Up if index % 2 == 0 else Qt.Key.Key_Down)
            pump(lambda: applied_preamp(expected), 'single live keyboard preamp edit')
            timings.append((time.monotonic() - started) * 1000)
            assert_preamp(expected)
        p95 = sorted(timings)[math.ceil(.95 * len(timings)) - 1]
        record('GUI single live preamp latency', samples=len(timings),
               samples_ms=[round(value, 2) for value in timings],
               median_ms=round(statistics.median(timings), 2), p95_ms=round(p95, 2),
               healthy_reference_target_ms=250, target_met=p95 <= 250,
               scope='keyboard event to verified worker completion; excludes extra independent readback')

        # A typed replacement and a longer burst exercise intermediate value signals,
        # the one-worker bound, retained keyboard focus, and final-value readback.
        editor = spin.lineEdit()
        QTest.keyClick(editor, Qt.Key.Key_A, Qt.KeyboardModifier.ControlModifier)
        QTest.keyClicks(editor, '-4.5')
        QTest.keyClick(editor, Qt.Key.Key_Return)
        pump(lambda: applied_preamp(-4.5), 'typed preamp value')
        assert_preamp(-4.5)
        baseline_calls = reload_count
        started = time.monotonic()
        for index in range(100):
            QTest.keyClick(spin, Qt.Key.Key_Up if index % 2 == 0 else Qt.Key.Key_Down)
            app.processEvents()
            time.sleep(.005)
        QTest.keyClick(editor, Qt.Key.Key_A, Qt.KeyboardModifier.ControlModifier)
        QTest.keyClicks(editor, '-3.5')
        QTest.keyClick(editor, Qt.Key.Key_Return)
        burst_seconds = time.monotonic() - started
        pump(lambda: applied_preamp(-3.5), 'final burst preamp value')
        assert_preamp(-3.5)
        accepted_calls = reload_count - baseline_calls
        assert maximum_in_flight == 1
        assert accepted_calls <= math.ceil(burst_seconds / .05) + 3
        record('GUI typed and burst preamp', edits=101, backend_updates=accepted_calls,
               maximum_in_flight=maximum_in_flight, final_db=spin.value(),
               edit_duration_ms=round(burst_seconds * 1000, 2), editor_focus_retained=True)
        manager.reload = original_reload

        window.tabs.setCurrentWidget(window.spatial)
        spatial_owner = window.audio_graph.spatial_manager
        for level in (25, 100, 75):
            window.spatial.wetdry_slider.setValue(level)
            pump(lambda: idle() and window.spatial._applied_state is not None
                 and window.spatial._applied_state['wet'] == level,
                 'live hybrid slider update')
            assert window.audio_graph.spatial_manager is spatial_owner
            spatial_owner.verify_controls_from_mapping(hybrid_controls(level/100), 2.0)
            assert window.audio_graph.is_path_verified()
            record('GUI hybrid level', level=level, holo_weight=.6, meier_weight=.4, owner_preserved=True)

        ticks_before = len(heartbeats)
        started = time.monotonic()
        window.close_completely()
        dispatch_ms = (time.monotonic() - started) * 1000
        pump(lambda: not window.isVisible() and window._graph_worker is None, 'asynchronous close')
        assert len(heartbeats) > ticks_before, 'Qt event loop did not run during close'
        assert not window.audio_graph.has_owned_modules
        wait_for(lambda: not any(s.name.startswith('eqspace.') for s in registry.snapshot().sinks),
                 'GUI owner cleanup')
        control.verify_playback_route('test.output', registry)
        gaps = [(right-left)*1000 for left, right in zip(heartbeats, heartbeats[1:])]
        record('GUI asynchronous close and responsiveness', close_dispatch_ms=round(dispatch_ms, 2),
               close_total_ms=round((time.monotonic()-started)*1000, 2),
               heartbeat_ticks=len(heartbeats), maximum_heartbeat_gap_ms=round(max(gaps, default=0), 2),
               scope='offscreen Qt event loop; independent synchronous verification also contributes to gaps')
    finally:
        heartbeat.stop()
        window._live_eq_timer.stop()
        window._live_spatial_timer.stop()
        window._live_eq_pending = False
        window._live_spatial_pending = None
        if window.audio_graph.has_owned_modules:
            pump(lambda: window._graph_worker is None, 'finish current worker before emergency cleanup')
            try:
                window.audio_graph.shutdown()
                window._shutdown_complete = True
            except Exception as exc:
                record('GUI emergency cleanup failed', error=str(exc))
        if not window.audio_graph.has_owned_modules:
            window.close_completely()
        else:
            window.hide()  # Do not schedule another worker during harness teardown.
        app.processEvents()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path)
    parser.add_argument('--require-limiter', action='store_true')
    parser.add_argument('--gui-only', action='store_true', help='Run GUI integration on a fresh private daemon, skipping backend permutations')
    args = parser.parse_args()
    root = (args.output or Path(tempfile.mkdtemp(prefix='eqspace-private-playback-'))).resolve()
    root.mkdir(parents=True, exist_ok=True)
    runtime = root / 'runtime'
    runtime.mkdir(mode=0o700, exist_ok=True)
    runtime.chmod(0o700)
    config = root / 'config'
    policy = config / 'wireplumber'
    if policy.exists():
        raise RuntimeError('use a new output directory for each run')
    shutil.copytree('/usr/share/wireplumber', policy)
    # Disable every hardware monitor before 90-enable-all executes (WP 0.4).
    (policy / 'main.lua.d' / '89-disable-hardware.lua').write_text(
        'alsa_monitor.enabled = false\nv4l2_monitor.enabled = false\nlibcamera_monitor.enabled = false\n')
    (policy / 'bluetooth.lua.d' / '89-disable-hardware.lua').write_text('bluez_monitor.enabled = false\n')
    daemon_config = root / 'private-pipewire.conf'
    daemon_config.write_text(PIPEWIRE_CONFIG)
    os.environ.update(QT_QPA_PLATFORM='offscreen', PIPEWIRE_RUNTIME_DIR=str(runtime), XDG_RUNTIME_DIR=str(runtime),
                      PIPEWIRE_REMOTE='eqspace-probe', XDG_CONFIG_HOME=str(config),
                      XDG_STATE_HOME=str(root / 'state'), XDG_CACHE_HOME=str(root / 'cache'),
                      WIREPLUMBER_CONFIG_DIR=str(policy))
    # A global module override must never leak into the daemon or native owners.
    os.environ.pop('PIPEWIRE_MODULE_DIR', None)
    logging.basicConfig(filename=root / 'graph.log', level=logging.INFO,
                        format='%(asctime)s %(levelname)s %(name)s %(message)s')
    processes, handles, checks = [], [], []
    graph = None
    player = None
    report = {'scope': 'private daemon, native modules, silent stream, null physical output',
              'audible_quality_verified': False, 'backend_permutations_requested': not args.gui_only,
              'checks': checks, 'passed': False}

    def spawn(name, command, **kwargs):
        log = (root / f'{name}.log').open('w')
        handles.append(log)
        child = subprocess.Popen(command, stdout=log, stderr=subprocess.STDOUT,
                                 start_new_session=True, **kwargs)
        processes.append(child)
        return child

    def record(name, **details):
        checks.append({'name': name, **details})
        print(json.dumps(checks[-1]), flush=True)
        (root / 'result.json').write_text(json.dumps(report, indent=2))

    try:
        from eqspace.core.pipewire.registry import PipeWireRegistry
        from eqspace.core.pipewire import control
        from eqspace.core.filterchain.manager import FilterChainManager, FilterSpec
        from eqspace.core.filterchain.limiter import detect_limiter_with_reason, lv2_host_directory
        from eqspace.core.filterchain.spatial import hybrid_controls
        from eqspace.core.dsp.spatial import HYBRID_DESCRIPTION, prepare_spatial
        from eqspace.core.profiles.presets import list_presets, load_preset
        from eqspace.ui.peq.peq_widget import _BAND_TYPE_LABELS
        from eqspace.ui.audio_graph import AudioGraphController

        daemon = spawn('pipewire', ['pipewire', '-c', str(daemon_config)])
        wait_for(lambda: (runtime / 'eqspace-probe').exists() or daemon.poll() is not None,
                 'private PipeWire socket')
        if daemon.poll() is not None:
            raise RuntimeError('private PipeWire exited; inspect pipewire.log')
        spawn('wireplumber', ['dbus-run-session', '--', 'wireplumber'])
        private_client_env = dict(os.environ)
        def traced_runner(command, timeout):
            # Pin every observation/mutation to this daemon even if a Qt object
            # outlives teardown; never fall back to the host process environment.
            result = subprocess.run(list(command), capture_output=True, text=True,
                                    timeout=timeout, check=True, env=private_client_env)
            output = result.stdout
            if command[0] in ('wpctl', 'pw-dump'):
                with (root / 'commands.jsonl').open('a') as evidence:
                    evidence.write(json.dumps({'time': time.monotonic(), 'command': list(command),
                                               'output': output}) + '\n')
            return output
        control.default_runner = traced_runner
        registry = PipeWireRegistry(runner=traced_runner)
        wait_for(lambda: any(s.name == 'test.output' for s in registry.snapshot().sinks), 'private null sink')
        # The private daemon must have no hardware output available at all.
        assert {s.name for s in registry.snapshot().sinks} == {'test.output'}
        wait_for(lambda: bool(subprocess.run(['pw-link', '-i'], capture_output=True, text=True,
                                             timeout=2).stdout.strip()), 'null sink ports')
        control.set_default_sink('test.output', registry=registry)
        zero = open('/dev/zero', 'rb')
        handles.append(zero)
        player = spawn('silent-player', ['pw-cat', '--playback', '--rate', '48000', '--channels', '2',
                               '--format', 'f32', '--properties', 'node.name=probe.player', '-'], stdin=zero)
        wait_for(lambda: any(n.name == 'probe.player' for n in registry.snapshot().streams), 'silent application')
        wait_for(lambda: control.verify_playback_route('test.output', registry) is None, 'initial direct route')
        capability, reason = detect_limiter_with_reason()
        if lv2_host_directory() is None:
            capability, reason = None, 'matching PipeWire LV2 host unavailable'
        if args.require_limiter and capability is None:
            raise RuntimeError(reason)
        report['limiter_available'] = capability is not None
        report['limiter_unavailable_reason'] = reason
        if not args.gui_only:
            current = [FilterChainManager()]
            graph = AudioGraphController(registry, lambda: 'test.output', lambda: current[0])
            graph.atomic_transitions = True
            hybrid_args, hybrid_peak, _ = prepare_spatial(
                {'profile': HYBRID_DESCRIPTION, 'wet': 75}, 48000)

            def configure(eq_on, spatial_on, limiter_on, preset=None, spatial_override=None):
                candidate = current[0]
                if eq_on:
                    specs = [FilterSpec('preamp', 'linear', {'Mult': .1, 'Add': 0})]
                    if preset:
                        specs.extend(FilterSpec(f'band_{i}', _BAND_TYPE_LABELS[b.band_type],
                                     {'Freq': b.freq_hz, 'Gain': b.gain_db, 'Q': b.q})
                                     for i, b in enumerate(preset.to_bands()) if b.enabled)
                    candidate = FilterChainManager(node_name=f'eqspace.probe.eq.{uuid.uuid4().hex[:10]}')
                    candidate.load(specs)
                    candidate.verify_controls(specs)
                selected_args, selected_peak = spatial_override or (hybrid_args, hybrid_peak)
                current[0] = graph.configure_playback(eq_enabled=eq_on, eq_candidate=candidate,
                    spatial_args=selected_args if spatial_on else None,
                    spatial_peak_db=selected_peak if spatial_on else 0,
                    limiter_enabled=limiter_on, limiter_capability=capability)
                assert graph.is_path_verified()
                assert {s.name for s in registry.snapshot().sinks if not s.name.startswith('eqspace.')} == {'test.output'}
                return graph.active_stages()

            combinations = list(itertools.product((False, True), repeat=3))
            for state in combinations:
                if state[2] and capability is None:
                    continue
                stages = configure(*state)
                record('effect combination', eq=state[0], spatial=state[1], limiter=state[2], stages=stages)
            for order in itertools.permutations(('eq', 'spatial', 'limiter')):
                if capability is None:
                    break
                configure(False, False, False)
                # Load a cached applied EQ before independent toggles, without routing it.
                current[0].load([FilterSpec('preamp', 'linear', {'Mult': .1, 'Add': 0})]) if not current[0].is_loaded else None
                for effect in order:
                    {'eq': graph.eq_on, 'spatial': lambda: graph.switch_spatial(hybrid_args, hybrid_peak),
                     'limiter': lambda: graph.set_limiter(True, capability)}[effect]()
                    current[0] = graph._active_eq_manager()
                    assert graph.is_path_verified()
                for effect in order:
                    {'eq': graph.eq_off, 'spatial': graph.spatial_off,
                     'limiter': lambda: graph.set_limiter(False)}[effect]()
                    current[0] = graph._active_eq_manager()
                    assert graph.is_path_verified()
                record('independent toggle order', order=order)
            stock_spatial_states = [
                {'profile': f'Crossfeed {mode}', 'crossfeed': True, 'crossfeed_mode': mode, 'wet': 100}
                for mode in ('Bauer', 'Meier', 'Strong')
            ] + [
                {'profile': 'HoloSpace 3D (Signature Spatial Immersion)', 'layout': 'HoloSpace 3D', 'wet': 100},
                {'profile': 'Studio Monitor (Nearfield ±30°)', 'layout': 'Stereo', 'wet': 100},
                {'profile': 'Cinema 7.1 Surround (Virtual Room)', 'layout': '7.1', 'stereo_expansion': True, 'wet': 100},
                {'profile': HYBRID_DESCRIPTION, 'wet': 100},
            ]
            for state in stock_spatial_states:
                prepared_args, prepared_peak, _ = prepare_spatial(state, 48000)
                for eq_on, limiter_on in itertools.product((False, True), repeat=2):
                    if limiter_on and capability is None:
                        continue
                    configure(eq_on, True, limiter_on, spatial_override=(prepared_args, prepared_peak))
                    record('stock Spatial profile', profile=state['profile'], eq=eq_on, limiter=limiter_on,
                           estimated_peak_db=prepared_peak)
            for name in list_presets():
                configure(True, True, capability is not None, load_preset(name))
                record('preset with hybrid preserved', preset=name)
            for level in (.25, 1, .75):
                graph.spatial_manager.update_controls(hybrid_controls(level))
                assert graph.is_path_verified()
                record('hybrid live controls', level=level, holo_weight=.6, meier_weight=.4)
            graph.shutdown()
            wait_for(lambda: not any(s.name.startswith('eqspace.') for s in registry.snapshot().sinks),
                     'owned modules disappeared after shutdown')
            control.verify_playback_route('test.output', registry)
            record('shutdown direct route and owner cleanup')
        exercise_window(registry, capability, record)
        report['passed'] = True
    except Exception as exc:
        report['player_exit_code'] = player.poll() if player else None
        if player is not None:
            for name, command in [('failure-pw-dump.json', ['pw-dump']), ('failure-links.txt', ['pw-link', '-l']), ('failure-ports.txt', ['pw-link', '-o'])]:
                try:
                    observation = subprocess.run(command, capture_output=True, text=True, timeout=3)
                    (root / name).write_text(observation.stdout + observation.stderr)
                except Exception:
                    pass
        report['error'] = f'{type(exc).__name__}: {exc}'
        report['traceback'] = traceback.format_exc()
        traceback.print_exc()
    finally:
        if graph and graph.has_owned_modules:
            try:
                graph.shutdown()
            except Exception as exc:
                report['cleanup_error'] = str(exc)
        # These process groups belong only to this private daemon/session.
        for child in reversed(processes):
            if child.poll() is None:
                os.killpg(child.pid, signal.SIGTERM)
                try:
                    child.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    os.killpg(child.pid, signal.SIGKILL)
                    child.wait(timeout=3)
        for handle in handles:
            handle.close()
        (root / 'result.json').write_text(json.dumps(report, indent=2))
    print(json.dumps({'passed': report['passed'], 'artifacts': str(root), 'checks': len(checks)}), flush=True)
    return 0 if report['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
