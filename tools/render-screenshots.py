#!/usr/bin/env python3
"""Render the real GUI with neutral demo data; never connect to live audio."""

import os
from pathlib import Path
import tempfile
from unittest.mock import patch


def main() -> int:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    with tempfile.TemporaryDirectory(prefix="eqspace-screenshots-") as directory:
        for variable, folder in (
            ("XDG_CONFIG_HOME", "config"), ("XDG_CACHE_HOME", "cache"),
            ("XDG_DATA_HOME", "data"), ("XDG_STATE_HOME", "state"),
        ):
            os.environ[variable] = str(Path(directory) / folder)
        from PySide6.QtCore import QThreadPool
        from PySide6.QtGui import QFont
        from PySide6.QtWidgets import QApplication
        from eqspace.core.pipewire.registry import PwNode, PwSnapshot
        from eqspace.core.profiles.presets import load_preset
        from eqspace.ui.main_window import MainWindow
        from eqspace.ui.spatial.spatial_widget import PROFILE_HYBRID

        class DemoRegistry:
            def snapshot(self):
                return PwSnapshot(
                    sinks=(PwNode(40, "bluez_output.demo", None, "Audio/Sink", .7, False,
                                  description="Wireless Headphones"),),
                    streams=(
                        PwNode(60, "demo.music", "Music Player", "Stream/Output/Audio", .8, False),
                        PwNode(61, "demo.browser", "Web Browser", "Stream/Output/Audio", .5, False),
                    ),
                )

            def graph_rate(self, required=False):
                return 48000.0

        class DemoManager:
            loaded = False
            node_name = "eqspace.demo"

            def unload(self):
                pass

        class DemoControl:
            def get_volume(self, node_id):
                return {40: .7, 60: .8, 61: .5}[node_id]

            def get_default_sink_name(self, **kwargs):
                return "bluez_output.demo"

            def is_system_routed(self, **kwargs):
                return False

            def get_active_output_links(self):
                return {
                    f"{name}:output_{channel}": [f"bluez_output.demo:playback_{channel}"]
                    for name in ("demo.music", "demo.browser") for channel in ("FL", "FR")
                }

        app = QApplication([])
        app.setFont(QFont("DejaVu Sans", 10))
        with patch("eqspace.ui.main_window.detect_limiter_with_reason", return_value=(None, "Optional LSP plugin not configured")), \
                patch.object(MainWindow, "_is_tray_available", return_value=False):
            window = MainWindow(registry=DemoRegistry(), filter_manager=DemoManager(),
                                poll_interval_ms=0, restore_profile=False)
        window.mixer.control = DemoControl()
        preset = load_preset("acoustic_presence_custom")
        window.peq.bands = preset.to_bands()
        window.peq.set_preamp(preset.preamp_db)
        window.peq._refresh_table()
        window.peq.table.setMinimumHeight(230)
        window.peq.update_curve()
        window.presets.set_draft_name(preset.display_name)
        window.presets.refresh(select=("builtin", "acoustic_presence_custom"))
        window.spatial.profile_combo.setCurrentText(PROFILE_HYBRID)
        window.hide_quick_start_banner()
        window.resize(1200, 920)
        window.show()
        app.processEvents()
        output = Path(__file__).resolve().parents[1] / "docs/screenshots"
        output.mkdir(parents=True, exist_ok=True)
        for name, widget in (
            ("main-window", window.peq), ("mixer", window.mixer),
            ("presets", window.presets), ("spatial", window.spatial),
        ):
            window.tabs.setCurrentWidget(widget)
            app.processEvents()
            if not window.grab().save(str(output / f"{name}.png")):
                raise RuntimeError(f"Could not save {name} screenshot")
        window.close()
        app.processEvents()
        QThreadPool.globalInstance().waitForDone()
        print("Rendered four current UI screenshots with isolated demo data.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
