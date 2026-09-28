"""Frozen launcher: keep bundled libraries out of host PipeWire processes."""
import os

# Qt/Python libraries are loaded from the bundle by their own paths. Host
# pw-cli/wpctl processes must use the host library search path.
if 'LD_LIBRARY_PATH_ORIG' in os.environ:
    os.environ['LD_LIBRARY_PATH'] = os.environ['LD_LIBRARY_PATH_ORIG']
else:
    os.environ.pop('LD_LIBRARY_PATH', None)

from eqspace.app import main

if __name__ == '__main__':
    raise SystemExit(main())
