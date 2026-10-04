#!/usr/bin/env python3
"""Login to Instagram via aiograpi-rest sidecar using credentials from the secret store.
Usage: login.py [proxy_url]"""

import json
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "_lib"))
from skill_http import api_base, die, get_secret  # noqa: E402

sidecar = api_base("INSTAGRAM_PRIVATE_API_URL", "http://localhost:8011")

user = get_secret("INSTAGRAM_USERNAME")
password = get_secret("INSTAGRAM_PASSWORD")
if not user or not password:
    die("✗ Instagram credentials not found in secret store\n"
        "  Save them with: social-profile-secrets/scripts/set-instagram.py <user> <pass>")

proxy = sys.argv[1] if len(sys.argv) > 1 else ""
print(f"Logging in to Instagram as {user} (locale=el_GR, tz=10800)...")
data = {"username": user, "password": password, "locale": "el_GR", "timezone": "10800"}  # noqa: S105
if proxy:
    data["proxy"] = proxy
    print(f"  Using proxy: {proxy}")

req = urllib.request.Request(f"{sidecar}/auth/login",
                             data=urllib.parse.urlencode(data).encode(), method="POST")
try:
    with urllib.request.urlopen(req, timeout=60) as r:
        resp = r.read().decode()
except urllib.error.URLError as exc:
    resp = exc.read().decode() if hasattr(exc, "read") and callable(exc.read) else str(exc)

if resp.strip().startswith('"') and resp.strip().endswith('"'):
    session_id = json.loads(resp)
    print("✓ Login successful!")
    print(f"  Session ID: {session_id[:8]}***")
    Path("/tmp/instagram_session_id").write_text(session_id)
    print("  Saved to /tmp/instagram_session_id")
else:
    print(f"Response: {resp}")
    try:
        d = json.loads(resp)
        exc = d.get("exc_type", "")
        if exc == "ChallengeRequired":
            print("✗ Challenge required — Instagram sent a security code via SMS/email.")
            print("  Use: login-2fa.py <code>  or  challenge-resolve.py <session_id> <last_json> <code>")
        elif exc == "TwoFactorRequired":
            print("✗ 2FA required — provide TOTP/SMS code.")
            print("  Use: login-2fa.py <code>")
        else:
            print(f'✗ Login failed: {d.get("detail", "unknown")}')
    except ValueError:
        pass
