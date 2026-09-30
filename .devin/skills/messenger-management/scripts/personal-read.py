#!/usr/bin/env python3
"""Read messages from a personal Messenger thread via browser bridge.
Usage: personal-read.py <account_id> <thread_id>"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "_lib"))
from skill_http import request, social_api, usage  # noqa: E402

if len(sys.argv) < 3:
    usage("personal-read.py <account_id> <thread_id>")
api, token = social_api()
print("Reading personal Messenger thread (requires noVNC session)...")
d = request("GET",
            f"{api}/api/v1/messenger/{sys.argv[1]}/personal/conversations/{sys.argv[2]}",
            token=token)
print(json.dumps(d, indent=2, ensure_ascii=False))
