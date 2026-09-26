"""UI-side extension of :class:`FilterChainManager` that loads pre-rendered args.

The spatial and mic renderers produce a complete single-line SPA properties
string via their ``render_args`` — the exact payload ``pw-cli load-module``
expects. ``FilterChainManager.load`` only accepts ``FilterSpec`` lists (the
PEQ path), so this subclass adds :meth:`load_args` which reuses the same
persistent ``pw-cli`` subprocess machinery. It lives in ``ui/`` because it
is presentation-layer glue; ``core/`` is untouched.
"""

from __future__ import annotations

import re
import os
import subprocess
import threading

from eqspace.core.filterchain.manager import COMMAND_TIMEOUT, FilterChainError, FilterChainManager


class ModuleArgsManager(FilterChainManager):
    """FilterChainManager that can load a pre-rendered module-args string."""

    def load_args(self, args: str, timeout: float = COMMAND_TIMEOUT) -> int:
        """Load ``libpipewire-module-filter-chain`` with *args* verbatim."""
        if self.is_loaded:
            raise FilterChainError("filter chain already loaded")
        if "'" in args:
            raise FilterChainError("module args must not contain single quotes")
        options = {}
        if re.search(r"type\s*=\s*lv2\b", args):
            from eqspace.core.filterchain.limiter import lv2_host_directory
            host = lv2_host_directory()
            if host is None:
                raise FilterChainError(
                    "PipeWire LV2 host is missing. Install the matching "
                    "libpipewire-module-filter-chain-lv2.so; see docs/lv2-host.md."
                )
            options["env"] = dict(os.environ, PIPEWIRE_MODULE_DIR=str(host))
        process = self._popen(
            ["pw-cli"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            **options,
        )
        try:
            self._read_until_prompt(process, timeout)
            assert process.stdin is not None
            process.stdin.write(
                f"load-module libpipewire-module-filter-chain '{args}'\n"
            )
            process.stdin.flush()
            output = self._read_output(process, timeout)
        except Exception:
            self._kill(process)
            raise
        match = re.search(r"@module:(\d+)", output)
        if not match:
            self._kill(process)
            raise FilterChainError(
                f"could not parse module id from pw-cli output: {output!r}"
            )
        self._module_id = int(match.group(1))
        self._process = process
        self._output_thread = threading.Thread(
            target=self._drain_output,
            args=(process,),
            name="eqspace-moduleargs-output",
            daemon=True,
        )
        self._output_thread.start()
        return self._module_id

    def update_args(self, args: str, timeout: float = COMMAND_TIMEOUT) -> int:
        """Reload with new args (unload + load)."""
        if self.is_loaded:
            self.unload(timeout=timeout)
        return self.load_args(args, timeout=timeout)
