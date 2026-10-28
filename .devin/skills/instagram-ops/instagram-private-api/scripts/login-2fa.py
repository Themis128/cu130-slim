#!/usr/bin/env python3
"""Login to Instagram with 2FA verification code.
Usage: login-2fa.py <verification_code> [proxy_url]"""

import json
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "_lib"))
from skill_http import api_base, die, get_secret, usage  # noqa: E402

sidecar = api_base("INSTAGRAM_PRIVATE_API_URL", "http://localhost:8011")
code = sys.argv[1] if len(sys.argv) > 1 else usage("login-2fa.py <verification_code> [proxy_url]")
proxy = sys.argv[2] if len(sys.argv) > 2 else ""

user = get_secret("INSTAGRAM_USERNAME")
password = get_secret("INSTAGRAM_PASSWORD")
if not user or not password:
    die("✗ Instagram credentials not found in secret store")

print("Logging in to Instagram with 2FA code...")
data = {"username": user, "password": password, "verification_code": code,
        "locale": "el_GR", "timezone": "10800"}
if proxy:
    data["proxy"] = proxy

req = urllib.request.Request(f"{sidecar}/auth/login",
                             data=urllib.parse.urlencode(data).encode(), method="POST")
try:
    with urllib.request.urlopen(req, timeout=60) as r:
        resp = r.read().decode()
except urllib.error.URLError as e:
    resp = e.read().decode() if hasattr(e, "read") else str(e)

if resp.strip().startswith('"'):
    session_id = json.loads(resp)
    print("✓ Login successful with 2FA!")
    print(f"  Session ID: {session_id[:20]}...")
    Path("/tmp/instagram_session_id").write_text(session_id)
else:
    print(f"Response: {resp}")
    print("✗ Login with 2FA failed")
