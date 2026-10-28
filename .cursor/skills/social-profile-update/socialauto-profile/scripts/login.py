#!/usr/bin/env python3
"""Log in to a platform's private API or browser session.
Usage: login.py <account-id> [2fa-code]"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "_lib"))
from skill_http import request, social_api, usage  # noqa: E402

account_id = sys.argv[1] if len(sys.argv) > 1 else usage("login.py <account-id> [2fa-code]")
body = {"verification_code": sys.argv[2]} if len(sys.argv) > 2 else {}
api, token = social_api()
d = request("POST", f"{api}/api/v1/profile/{account_id}/login", token=token, data=body)
print(json.dumps(d, indent=2, ensure_ascii=False))
