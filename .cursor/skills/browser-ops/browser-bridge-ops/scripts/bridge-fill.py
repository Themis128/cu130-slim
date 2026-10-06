#!/usr/bin/env python3
"""Fill a form field in the browser by CSS selector.
Usage: bridge-fill.py <selector> <value>"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "_lib"))
from skill_http import api_base, request, usage  # noqa: E402

bridge = api_base("BROWSER_BRIDGE_URL", "http://localhost:9223")
if len(sys.argv) < 3:
    usage("bridge-fill.py <selector> <value>")
d = request(
    "POST",
    f"{bridge}/session/fill",
    data={"selector": sys.argv[1], "value": sys.argv[2]},
)
print(f'Status: {d.get("status", "?")}')
print(f'Filled: {d.get("filled", False)}')
