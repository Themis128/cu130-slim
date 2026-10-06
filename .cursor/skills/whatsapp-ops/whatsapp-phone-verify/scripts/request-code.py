#!/usr/bin/env python3
"""Request WhatsApp verification code via SMS or voice.
Usage: request-code.py <account_id> [SMS|VOICE] [language]"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import request_code  # noqa: E402
from skill_http import usage  # noqa: E402

account_id = sys.argv[1] if len(sys.argv) > 1 else \
    usage("request-code.py <account_id> [SMS|VOICE] [language]")
method = sys.argv[2] if len(sys.argv) > 2 else "SMS"
language = sys.argv[3] if len(sys.argv) > 3 else "en_US"
print(f"=== Requesting verification code for account {account_id} ===", file=sys.stderr)
print(f"Method: {method}, Language: {language}", file=sys.stderr)
print(json.dumps(request_code(account_id, method, language), indent=2, ensure_ascii=False))
