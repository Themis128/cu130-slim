#!/usr/bin/env python3
"""Navigate the browser to a URL.
Usage: bridge-navigate.py <url>"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "_lib"))
from skill_http import api_base, request, usage  # noqa: E402

bridge = api_base("BROWSER_BRIDGE_URL", "http://localhost:9223")
url = sys.argv[1] if len(sys.argv) > 1 else usage("bridge-navigate.py <url>")
d = request("POST", f"{bridge}/session/navigate", data={"url": url})
print(f'URL: {d.get("url", "?")}')
print(f'Title: {d.get("title", "?")}')
