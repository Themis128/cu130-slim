#!/usr/bin/env python3
"""Get current page info (URL and title)."""

import urllib.error
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "_lib"))
from skill_http import api_base  # noqa: E402

import json
import urllib.request

bridge = api_base("BROWSER_BRIDGE_URL", "http://localhost:9223")
try:
    with urllib.request.urlopen(f"{bridge}/session/page-info", timeout=30) as r:
        d = json.loads(r.read())
    print(f'URL: {d.get("url", "?")}')
    print(f'Title: {d.get("title", "?")}')
except (urllib.error.URLError, ValueError):
    print("No active session")
