#!/usr/bin/env python3
"""Take a screenshot of the browser viewport.
Saves to /tmp/browser-screenshot.png by default.
Usage: bridge-screenshot.py [output_path]"""

import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "_lib"))
from skill_http import api_base  # noqa: E402

bridge = api_base("BROWSER_BRIDGE_URL", "http://localhost:9223")

print("Browser screenshot not available via API.")
print("View the browser at: http://localhost:6080/vnc.html")
print("Current page:")
try:
    with urllib.request.urlopen(f"{bridge}/session/page-info", timeout=30) as r:
        d = json.loads(r.read())
    print(f'  URL: {d.get("url", "?")}')
    print(f'  Title: {d.get("title", "?")}')
except (urllib.error.URLError, ValueError):
    print("  No active session")
