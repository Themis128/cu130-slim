#!/usr/bin/env python3
"""Start Instagram browser session and wait for VNC login.
Usage: start-session.py"""

import json
import sys
import time
import urllib.request
from pathlib import Path

BRIDGE = "http://localhost:9223"


def post(path: str, data: dict) -> dict:
    req = urllib.request.Request(
        f"{BRIDGE}{path}", data=json.dumps(data).encode(),
        headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read().decode())
    except Exception:
        return {}


print("Stopping any existing browser session...")
post("/session/stop", {})
time.sleep(2)

print("Starting Instagram browser session...")
print(json.dumps(post("/session/start", {"platform": "instagram"}), indent=2))

print("""
========================================
  Open this URL to log in via VNC:
  http://localhost:6080/vnc.html
========================================

Click 'Log in with Facebook' on the Instagram login page.
Select the correct account if multiple appear.
Once you see your Instagram feed, run:

  python3 check-session.py

to verify the login.""")
