#!/usr/bin/env python3
"""List personal Messenger conversations via browser bridge.
Usage: personal-conversations.py <account_id>"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "_lib"))
from skill_http import request, social_api, usage  # noqa: E402

account_id = sys.argv[1] if len(sys.argv) > 1 else usage("personal-conversations.py <account_id>")
api, token = social_api()
print("Reading personal Messenger conversations (requires noVNC session)...")
d = request("GET", f"{api}/api/v1/messenger/{account_id}/personal/conversations", token=token)
print(json.dumps(d, indent=2, ensure_ascii=False))
