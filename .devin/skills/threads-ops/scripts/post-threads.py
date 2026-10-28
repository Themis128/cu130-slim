#!/usr/bin/env python3
"""Publish a simple text thread to the first connected Threads account.
Usage: post-threads.py "Thread text" [account_id]"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import accounts, team_token  # noqa: E402
from skill_http import request, usage  # noqa: E402

text = sys.argv[1] if len(sys.argv) > 1 else usage('post-threads.py "Thread text" [account_id]')
account_id = sys.argv[2] if len(sys.argv) > 2 else ""

api, token = team_token()

if not account_id:
    account_id = next((a["id"] for a in accounts(token)
                       if a.get("platform") == "threads" and a.get("status") == "active"), "")
if not account_id:
    print("No active Threads account found. Connect one first:")
    print("  python3 .devin/skills/threads-ops/scripts/connect-threads.py --open")
    sys.exit(1)

print(f"=== Creating draft on Threads account {account_id} ===")
created = request("POST", f"{api}/api/v1/content/posts", token=token,
                  data={"content_text": text, "target_account_ids": [account_id]})
print(json.dumps(created, indent=2))
post_id = created.get("id", "")
if not post_id:
    print("Draft creation failed", file=sys.stderr)
    sys.exit(1)

print(f"\n=== Publishing post {post_id} now ===")
print(json.dumps(request("POST", f"{api}/api/v1/content/posts/{post_id}/publish-now",
                         token=token), indent=2))
