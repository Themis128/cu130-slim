#!/usr/bin/env python3
"""Click an element in the browser by CSS selector.
Usage: bridge-click.py <selector>"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "_lib"))
from skill_http import api_base, request, usage  # noqa: E402

bridge = api_base("BROWSER_BRIDGE_URL", "http://localhost:9223")
selector = sys.argv[1] if len(sys.argv) > 1 else usage("bridge-click.py <selector>")
d = request("POST", f"{bridge}/session/click", data={"selector": selector})
print(f'Status: {d.get("status", "?")}')
print(f'Clicked: {d.get("clicked", False)}')
