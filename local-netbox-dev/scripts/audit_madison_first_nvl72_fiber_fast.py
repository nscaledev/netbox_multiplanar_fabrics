from __future__ import annotations

from pathlib import Path
import sys


for candidate in (
    Path('/opt/netbox/local-plugins/netbox_multiplanar_fabrics/local-netbox-dev/scripts'),
    Path('/Users/mencken/github-repos/netbox_multiplanar_fabrics/local-netbox-dev/scripts'),
    Path(globals().get('__file__', '.')).resolve().parent,
):
    if candidate.exists() and str(candidate) not in sys.path:
        sys.path.insert(0, str(candidate))

print('Compatibility audit: delegating to report_madison_first_nvl72_fiber_paths.py.')

import report_madison_first_nvl72_fiber_paths  # noqa: E402,F401
