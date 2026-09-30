#!/usr/bin/env python3
"""Send a personal Messenger message via browser bridge.
Usage: personal-send.py <account_id> <thread_id> <text>"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "_lib"))
from skill_http import request, social_api, usage  # noqa: E402

if len(sys.argv) < 4:
    usage("personal-send.py <account_id> <thread_id> <text>")
api, token = social_api()
print("Sending personal Messenger message (requires noVNC session)...")
d = request("POST", f"{api}/api/v1/messenger/{sys.argv[1]}/personal/send", token=token,
            data={"thread_id": sys.argv[2], "text": sys.argv[3]})
print(json.dumps(d, indent=2, ensure_ascii=False))
