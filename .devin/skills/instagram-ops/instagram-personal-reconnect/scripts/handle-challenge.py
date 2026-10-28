#!/usr/bin/env python3
"""Resolve a challenge (SMS/email verification) with a security code.
Usage: handle-challenge.py <session_id> <last_json> <security_code>"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import parse_login_result, sidecar_post  # noqa: E402
from skill_http import usage  # noqa: E402

if len(sys.argv) < 4:
    usage("handle-challenge.py <session_id> <last_json> <security_code>")
session_id, last_json, code = sys.argv[1], sys.argv[2], sys.argv[3]

print("=== Resolving challenge ===", file=sys.stderr)
import json
raw = sidecar_post("/auth/challenge/resolve",
                   {"last_json": json.loads(last_json), "security_code": code},
                   session_id=session_id, json_body=True)
status, detail = parse_login_result(raw)
print(status)
if detail:
    print(detail)
