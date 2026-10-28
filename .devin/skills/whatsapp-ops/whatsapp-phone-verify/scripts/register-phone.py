#!/usr/bin/env python3
"""Register WhatsApp phone number for Cloud API use.
Usage: register-phone.py <account_id> [6-digit-pin]"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import register_phone  # noqa: E402
from skill_http import usage  # noqa: E402

account_id = sys.argv[1] if len(sys.argv) > 1 else usage("register-phone.py <account_id> [6-digit-pin]")
pin = sys.argv[2] if len(sys.argv) > 2 else ""
print(f"=== Registering phone for account {account_id} ===", file=sys.stderr)
print(json.dumps(register_phone(account_id, pin), indent=2, ensure_ascii=False))
