#!/usr/bin/env python3
"""Start a browser session in the browser-novnc container and navigate to
the platform's login page. Prints the noVNC URL for the user to open.

Usage:
  start-login.py <platform>   (twitter, threads, tiktok, instagram)"""

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import LOGIN_URLS, post  # noqa: E402

platform = sys.argv[1] if len(sys.argv) > 1 else ""
if platform not in LOGIN_URLS:
    print("Usage: start-login.py <platform>", file=sys.stderr)
    print("Platforms: twitter, threads, tiktok, instagram", file=sys.stderr)
    sys.exit(1)

login_url = LOGIN_URLS[platform]

print(f"=== Starting {platform} login session ===", file=sys.stderr)

# Stop any existing session
post("/session/stop", {})
time.sleep(1)

# Start a new session for the platform
start = post("/session/start", {"platform": platform})
novnc_path = start.get("novnc_url", "/novnc/vnc.html?autoconnect=1&resize=scale")

time.sleep(2)

print(f"Navigating to {login_url}...", file=sys.stderr)
post("/session/navigate", {"url": login_url})
time.sleep(3)

for line in ("", "========================================",
             f"  noVNC URL: http://localhost:6080{novnc_path}",
             "========================================", "",
             f"Open the noVNC URL in your browser and complete the login for {platform}.",
             f"Then run: python3 wait-for-login.py {platform}", "",
             f"http://localhost:6080{novnc_path}"):
    print(line, file=sys.stderr)
