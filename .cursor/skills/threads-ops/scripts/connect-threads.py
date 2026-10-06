#!/usr/bin/env python3
"""Start the SocialAuto OAuth flow for Threads and print the
VNC/authorize URL. Pass --open to auto-open in browser-novnc."""

import json
import sys
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import TEAM_ID, api, bridge  # noqa: E402
from skill_http import api_login, request  # noqa: E402

base = api()
print("=== Logging in to SocialAuto ===")
token = api_login(base)

print("=== Generating Threads OAuth URL ===")
data = request("GET", f"{base}/api/v1/auth/oauth/threads/authorize?team_id={TEAM_ID}",
               token=token)
auth_url = data.get("authorization_url", "")
if not auth_url:
    print("Could not generate OAuth URL", file=sys.stderr)
    sys.exit(1)

print(f"\nAuthorize URL: {auth_url}\n")

if len(sys.argv) > 1 and sys.argv[1] == "--open":
    print("=== Opening in browser-novnc ===")
    print(json.dumps(bridge("/session/navigate", {"url": auth_url}), indent=2))
    print("\nOpen noVNC at http://localhost:6080/vnc.html to see the consent dialog.")
else:
    print("To auto-open in the VNC browser, run:")
    print("  connect-threads.py --open\n")
    print("Open noVNC at http://localhost:6080/vnc.html and navigate to "
          "the Authorize URL above.")
