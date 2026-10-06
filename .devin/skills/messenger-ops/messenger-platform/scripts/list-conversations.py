#!/usr/bin/env python3
"""List Messenger conversations for a Facebook Page.
Usage: list-conversations.py [account_id] [--limit 25] [--platform messenger]"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "_lib"))
from skill_http import request, social_api  # noqa: E402

p = argparse.ArgumentParser()
p.add_argument("account_id", nargs="?", default="3f2f59c4-f190-44ad-aefe-4321af08ef89")
p.add_argument("--limit", type=int, default=25)
p.add_argument("--platform", default="messenger")
a = p.parse_args()

api, token = social_api()
data = request("GET",
               f"{api}/api/v1/messenger/{a.account_id}/conversations"
               f"?limit={a.limit}&platform={a.platform}", token=token)
if not data:
    print("No conversations found.")
    raise SystemExit(0)
for c in data:
    names = [p_.get("name", p_.get("id", "?")) for p_ in (c.get("participants") or [])]
    print(f'ID:       {c.get("id", "?")}')
    print(f'  People:  {", ".join(names)}')
    print(f'  Snippet: {c.get("snippet", "")}')
    print(f'  Updated: {c.get("updated_time", "?")}')
    if c.get("unread_count"):
        print(f'  Unread:  {c["unread_count"]}')
    print()
