#!/usr/bin/env python3
"""Resolve an Instagram challenge with a security code.
Usage: challenge-resolve.py <session_id> <last_json> <security_code>"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "_lib"))
from skill_http import api_base, request, usage  # noqa: E402

if len(sys.argv) < 4:
    usage("challenge-resolve.py <session_id> <last_json> <security_code>")
session_id, last_json, code = sys.argv[1], sys.argv[2], sys.argv[3]
sidecar = api_base("INSTAGRAM_PRIVATE_API_URL", "http://localhost:8011")

print("Resolving Instagram challenge...")
resp = request("POST", f"{sidecar}/auth/challenge/resolve",
               headers={"X-Session-ID": session_id},
               data={"last_json": last_json, "security_code": code})
print(f"Response: {resp}")
if "true" in str(resp).lower():
    print("✓ Challenge resolved successfully!")
    print("  Retry login now: instagram-private-api/scripts/login.py")
else:
    print("✗ Challenge resolution failed")
