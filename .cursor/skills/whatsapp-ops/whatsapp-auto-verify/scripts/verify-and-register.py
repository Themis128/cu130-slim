#!/usr/bin/env python3
"""Verify a WhatsApp verification code and register the phone number.
Usage: verify-and-register.py <account_id> <6-digit-code> [6-digit-pin]"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "_lib"))
from skill_http import request_status, social_api, usage  # noqa: E402

if len(sys.argv) < 3:
    usage("verify-and-register.py <account_id> <6-digit-code> [6-digit-pin]")
account_id, code = sys.argv[1], sys.argv[2]
pin = sys.argv[3] if len(sys.argv) > 3 else ""

api, token = social_api()
base = f"{api}/api/v1/whatsapp/{account_id}/phone"


def ok(resp_body: str) -> bool:
    try:
        d = json.loads(resp_body)
    except ValueError:
        return False
    if d.get("status") == "ok" or d.get("success") or d.get("verified"):
        return True
    detail = d.get("detail")
    return not (isinstance(detail, str) and "error" in detail.lower())


print("=== Verifying code ===", file=sys.stderr)
_, body = request_status("POST", f"{base}/verify-code", token=token, data={"code": code})
if not ok(body):
    print("❌ Code verification failed:", file=sys.stderr)
    try:
        print(json.dumps(json.loads(body), indent=2))
    except ValueError:
        print(body)
    sys.exit(1)
print("✅ Code verified", file=sys.stderr)

print("=== Registering phone number ===", file=sys.stderr)
_, body = request_status("POST", f"{base}/register", token=token,
                         data={"pin": pin} if pin else {})
if not ok(body):
    print("⚠️ Registration response:", file=sys.stderr)
    try:
        print(json.dumps(json.loads(body), indent=2))
    except ValueError:
        print(body)
print("✅ Registration complete", file=sys.stderr)

print("\n=== Final phone status ===", file=sys.stderr)
_, body = request_status("GET", f"{base}/status", token=token)
try:
    print(json.dumps(json.loads(body), indent=2))
except ValueError:
    print(body)
