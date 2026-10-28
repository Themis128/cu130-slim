#!/usr/bin/env python3
"""Verify WhatsApp phone number with the received code.
Usage: verify-code.py <account_id> <6-digit-code>"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import verify_code  # noqa: E402
from skill_http import usage  # noqa: E402

if len(sys.argv) < 3:
    usage("verify-code.py <account_id> <6-digit-code>")
print(f"=== Verifying code for account {sys.argv[1]} ===", file=sys.stderr)
print(json.dumps(verify_code(sys.argv[1], sys.argv[2]), indent=2, ensure_ascii=False))
