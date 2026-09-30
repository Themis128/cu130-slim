#!/usr/bin/env python3
"""Check browser bridge health and current session status."""

import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "_lib"))
from skill_http import api_base  # noqa: E402

bridge = api_base("BROWSER_BRIDGE_URL", "http://localhost:9223")


def show(title: str, path: str, fallback: str) -> None:
    print(title)
    try:
        with urllib.request.urlopen(f"{bridge}{path}", timeout=30) as r:
            print(json.dumps(json.loads(r.read()), indent=2))
    except (urllib.error.URLError, ValueError):
        print(fallback)
    print()


show("=== Bridge health ===", "/health", "Bridge not responding")
show("=== Session status ===", "/session/status", "No active session")
show("=== Page info ===", "/session/page-info", "No page info")
