"""Lightweight audio level monitor for PipeWire sources without DSP in Python.

Queries node level/volume via wpctl or injected level providers to display
input/output meters in the GUI without touching real-time audio streams.
"""

from __future__ import annotations

import re
from typing import Callable, Optional

from .registry import Runner, default_runner


class PipeWireLevelMonitor:
    """Queries input activity or volume levels for GUI metering."""

    def __init__(
        self,
        node_id: Optional[int] = None,
        runner: Optional[Runner] = None,
        level_fn: Optional[Callable[[], float]] = None,
    ) -> None:
        self.node_id = node_id
        self._runner = runner or default_runner
        self._level_fn = level_fn

    def get_level(self) -> float:
        """Return normalized activity level in [0.0, 1.0]."""
        if self._level_fn is not None:
            try:
                raw = float(self._level_fn())
                return max(0.0, min(1.0, raw))
            except Exception:
                return 0.0

        if self.node_id is None:
            return 0.0

        try:
            out = self._runner(["wpctl", "get-volume", str(self.node_id)], timeout=1.0)
            if "[MUTED]" in out:
                return 0.0
            match = re.search(r"Volume:\s*([0-9.]+)", out)
            if match:
                val = float(match.group(1))
                return max(0.0, min(1.0, val))
        except Exception:
            return 0.0

        return 0.0
