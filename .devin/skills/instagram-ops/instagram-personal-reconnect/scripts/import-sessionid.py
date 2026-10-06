#!/usr/bin/env python3
"""Import an Instagram session by sessionid cookie (bypasses password login).
Use this when the password login is blocked by Instagram's version check.

Usage: import-sessionid.py <account_id> <sessionid>"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import parse_login_result, sidecar_healthy, sidecar_post  # noqa: E402
from skill_http import usage  # noqa: E402

if len(sys.argv) < 3:
    usage("import-sessionid.py <account_id> <sessionid>")
account_id, sessionid_cookie = sys.argv[1], sys.argv[2]

if not sidecar_healthy():
    print("❌ Sidecar is not healthy.", file=sys.stderr)
    sys.exit(1)

print("=== Importing session via sessionid ===", file=sys.stderr)
raw = sidecar_post("/auth/login/by/sessionid", {"sessionid": sessionid_cookie})
status, detail = parse_login_result(raw)
print(status)
if detail:
    print(detail)
