#!/usr/bin/env python3
"""Get or set AI auto-reply config for a personal Messenger account.
Usage: personal-auto-reply.py <account_id> [get|set] [enabled] [system_prompt]"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "_lib"))
from skill_http import die, request, social_api, usage  # noqa: E402

if len(sys.argv) < 2:
    usage("personal-auto-reply.py <account_id> [get|set] [enabled] [system_prompt]")
account_id = sys.argv[1]
action = sys.argv[2] if len(sys.argv) > 2 else "get"
api, token = social_api()
url = f"{api}/api/v1/messenger/{account_id}/personal/auto-reply"

if action == "get":
    d = request("GET", url, token=token)
elif action == "set":
    if len(sys.argv) < 4:
        usage("personal-auto-reply.py <account_id> set <true|false> [system_prompt]")
    prompt = sys.argv[4] if len(sys.argv) > 4 else \
        "You are a helpful assistant. Reply concisely and professionally."
    d = request("PUT", url, token=token,
                data={"enabled": sys.argv[3].lower() == "true", "system_prompt": prompt})
else:
    die(f"Unknown action: {action} (use 'get' or 'set')")
print(json.dumps(d, indent=2, ensure_ascii=False))
