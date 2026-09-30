#!/usr/bin/env python3
"""Get messages in a conversation thread.
Usage: get-messages.py <account_id> <conversation_id> [--limit 20]"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "_lib"))
from skill_http import request, social_api  # noqa: E402

p = argparse.ArgumentParser()
p.add_argument("account_id")
p.add_argument("conversation_id")
p.add_argument("--limit", type=int, default=20)
a = p.parse_args()

api, token = social_api()
data = request("GET",
               f"{api}/api/v1/messenger/{a.account_id}/conversations/"
               f"{a.conversation_id}?limit={a.limit}", token=token)
if not data:
    print("No messages found.")
    raise SystemExit(0)
for m in data:
    print(f'[{m.get("created_time", "?")}] {m.get("from_id", "?")}: {m.get("message", "")}')
