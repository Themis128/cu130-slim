#!/usr/bin/env python3
"""Check WhatsApp phone registration status.
Usage: check-phone-status.py <account_id>"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import phone_status  # noqa: E402
from skill_http import usage  # noqa: E402

account_id = sys.argv[1] if len(sys.argv) > 1 else usage("check-phone-status.py <account_id>")
print(f"=== WhatsApp Phone Status for account {account_id} ===", file=sys.stderr)
print(json.dumps(phone_status(account_id), indent=2, ensure_ascii=False))
