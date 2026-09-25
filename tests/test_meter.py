"""Tests for PipeWire audio level monitor."""

import pytest
from eqspace.core.pipewire.meter import PipeWireLevelMonitor


class TestPipeWireLevelMonitor:
    def test_default_level(self):
        monitor = PipeWireLevelMonitor(level_fn=lambda: 0.75)
        assert monitor.get_level() == 0.75

    def test_clamped_level(self):
        monitor_high = PipeWireLevelMonitor(level_fn=lambda: 1.5)
        assert monitor_high.get_level() == 1.0

        monitor_low = PipeWireLevelMonitor(level_fn=lambda: -0.2)
        assert monitor_low.get_level() == 0.0

    def test_fallback_on_error(self):
        def bad_fn():
            raise RuntimeError("query failed")

        monitor = PipeWireLevelMonitor(level_fn=bad_fn)
        assert monitor.get_level() == 0.0
