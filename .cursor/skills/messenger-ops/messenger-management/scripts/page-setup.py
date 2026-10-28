#!/usr/bin/env python3
"""Set up Messenger on a Facebook Page.
Usage: page-setup.py <account_id> [greeting_text]"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "_lib"))
from skill_http import request, social_api, usage  # noqa: E402

account_id = sys.argv[1] if len(sys.argv) > 1 else usage("page-setup.py <account_id> [greeting_text]")
body = {"greeting_text": sys.argv[2]} if len(sys.argv) > 2 else {}
api, token = social_api()
print(f"Setting up Messenger on account {account_id}...")
d = request("POST", f"{api}/api/v1/messenger/{account_id}/setup", token=token, data=body)
print(json.dumps(d, indent=2, ensure_ascii=False))
