#!/usr/bin/env python3
"""Verify that the Instagram private API session is active by calling
the sidecar's profile endpoint.

Usage: verify.py <account_id>"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import psql, sidecar_get  # noqa: E402
from skill_http import usage  # noqa: E402

account_id = sys.argv[1] if len(sys.argv) > 1 else usage("verify.py <account_id>")

session_id = psql(f"""SELECT meta_data->>'private_api_session_id'
FROM social_accounts
WHERE id = '{account_id}';""", tuples_only=True).strip()

if not session_id or session_id == "null":
    print(f"❌ No session_id found for account {account_id}", file=sys.stderr)
    print("Run login.py first to create a session.", file=sys.stderr)
    sys.exit(1)

print("=== Verifying session ===", file=sys.stderr)
raw = sidecar_get("/auth/profile", session_id=session_id)
username = ""
try:
    d = json.loads(raw)
    username = d.get("username") or d.get("user", {}).get("username", "")
except ValueError:
    pass

if username:
    print(f"✅ Session active for @{username}", file=sys.stderr)
else:
    print("❌ Session not active or expired", file=sys.stderr)
    print(raw[:500], file=sys.stderr)
    sys.exit(1)
