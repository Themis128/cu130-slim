#!/usr/bin/env python3
"""Start a browser session for a platform.
Usage: bridge-start-session.py <platform>"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "_lib"))
from skill_http import api_base, request, usage  # noqa: E402

bridge = api_base("BROWSER_BRIDGE_URL", "http://localhost:9223")
platform = sys.argv[1] if len(sys.argv) > 1 else usage("bridge-start-session.py <platform>")
d = request("POST", f"{bridge}/session/start", data={"platform": platform})
print(f'Platform: {d.get("platform", "?")}')
print(f'Status: {d.get("status", "?")}')
print(f'Message: {d.get("message", "?")}')
print(f'noVNC: {d.get("novnc_url", "?")}')
