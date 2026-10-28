#!/usr/bin/env python3
"""Complete 2FA login with a verification code.
Usage: handle-2fa.py <account_id> <verification_code>"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import get_credentials, parse_login_result, sidecar_post  # noqa: E402
from skill_http import usage  # noqa: E402

if len(sys.argv) < 3:
    usage("handle-2fa.py <account_id> <verification_code>")
account_id, code = sys.argv[1], sys.argv[2]
username, password = get_credentials(account_id)

print("=== Completing 2FA login ===", file=sys.stderr)
raw = sidecar_post("/auth/login", {
    "username": username, "password": password,
    "verification_code": code, "locale": "el_GR", "timezone": "10800"})
status, detail = parse_login_result(raw)
print(status)
if detail:
    print(detail)
