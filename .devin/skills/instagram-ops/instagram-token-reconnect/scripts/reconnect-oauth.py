#!/usr/bin/env python3
"""Generate the Instagram OAuth reconnection URL and open it in the
browser bridge.
Usage: reconnect-oauth.py <account_id>"""

import json
import sys
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "_lib"))
from skill_http import request, social_api, usage  # noqa: E402

account_id = sys.argv[1] if len(sys.argv) > 1 else usage("reconnect-oauth.py <account_id>")
api, token = social_api()

print(f"=== Instagram OAuth Reconnection for account {account_id} ===", file=sys.stderr)
data = request("GET", f"{api}/api/v1/auth/oauth/instagram/authorize", token=token)
url = data.get("authorization_url") or data.get("url") or data.get("auth_url", "")
if not url:
    print("Error: Could not get OAuth URL from SocialAuto", file=sys.stderr)
    sys.exit(1)

print(f"OAuth URL: {url}\nOpening in browser-novnc...", file=sys.stderr)
req = urllib.request.Request(
    "http://localhost:9223/session/navigate",
    data=json.dumps({"url": url}).encode(),
    headers={"Content-Type": "application/json"}, method="POST")
try:
    urllib.request.urlopen(req, timeout=15)
except Exception:
    pass

print("Browser navigated to OAuth URL.", file=sys.stderr)
print("Complete the login via noVNC at http://localhost:6080/vnc.html", file=sys.stderr)
print("After granting permissions, the callback will store the new token.", file=sys.stderr)
