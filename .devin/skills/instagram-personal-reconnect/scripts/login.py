#!/usr/bin/env python3
"""Login to Instagram via the instagrapi sidecar using stored credentials.
Handles 2FA and challenge responses by reporting the required action.

Usage: login.py <account_id>"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import (get_credentials, parse_login_result,  # noqa: E402
                     sidecar_healthy, sidecar_post)
from skill_http import usage  # noqa: E402

account_id = sys.argv[1] if len(sys.argv) > 1 else usage("login.py <account_id>")

if not sidecar_healthy():
    print("❌ Sidecar is not healthy. Start the instagram-private-api container.",
          file=sys.stderr)
    sys.exit(1)

username, password = get_credentials(account_id)
print(f"=== Logging in to Instagram as {username} ===", file=sys.stderr)
raw = sidecar_post("/auth/login", {
    "username": username, "password": password,
    "locale": "el_GR", "timezone": "10800"})
status, detail = parse_login_result(raw)
print(status)
if detail:
    print(detail)
