#!/usr/bin/env python3
"""Login to Instagram sidecar using a sessionid.
Usage: login-by-sessionid.py <sessionid>"""

import json
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import sidecar  # noqa: E402
from skill_http import usage  # noqa: E402

sid = sys.argv[1] if len(sys.argv) > 1 else usage("login-by-sessionid.py <sessionid>")
api = sidecar()

print("Logging in to Instagram via sessionid...")

def post(form: dict | None = None, json_body: dict | None = None) -> str:
    if json_body is not None:
        body, ctype = json.dumps(json_body).encode(), "application/json"
    else:
        body, ctype = urllib.parse.urlencode(form).encode(), "application/x-www-form-urlencoded"
    req = urllib.request.Request(f"{api}/auth/login/by/sessionid", data=body,
                                 headers={"Content-Type": ctype}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.read().decode()
    except urllib.error.URLError as e:
        return e.read().decode() if hasattr(e, "read") else str(e)


resp = post(form={"sessionid": sid})
try:
    json.loads(resp)
except ValueError:
    print("Login failed. Trying JSON body...")
    resp = post(json_body={"sessionid": sid})

try:
    d = json.loads(resp)
    sid_out = d.get("session_id") or d.get("sessionid") or "N/A"
    print(f"Session ID: {sid_out}")
    print(f'Status: {d.get("status", "ok")}')
    user = d.get("user", {})
    if user:
        print(f'Username: {user.get("username", "N/A")}')
        print(f'PK: {user.get("pk", "N/A")}')
        print(f'Full name: {user.get("full_name", "N/A")}')
        print(f'Biography: {user.get("biography", "N/A")[:80]}')
except ValueError:
    print(resp)
