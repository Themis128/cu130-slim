#!/usr/bin/env python3
"""Update Instagram bio via browser-novnc bridge.
Usage: update-bio.py "Your bio text here" """

import json
import sys
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "_lib"))
from skill_http import usage  # noqa: E402

BRIDGE = "http://localhost:9223"
bio = sys.argv[1] if len(sys.argv) > 1 else usage('update-bio.py "Your bio text"')


def req(method: str, path: str, data: dict | None = None) -> dict:
    r = urllib.request.Request(
        f"{BRIDGE}{path}",
        data=json.dumps(data).encode() if data else None,
        headers={"Content-Type": "application/json"}, method=method)
    try:
        with urllib.request.urlopen(r, timeout=30) as resp:
            return json.loads(resp.read().decode())
    except Exception:
        return {}


print("Updating Instagram bio via browser bridge...")
print(json.dumps(req("PATCH", "/profile/instagram", {"bio": bio}), indent=2))
print("\nVerifying...")
time.sleep(5)
print(json.dumps(req("GET", "/profile/instagram"), indent=2))
