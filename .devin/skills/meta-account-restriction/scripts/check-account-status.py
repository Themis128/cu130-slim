#!/usr/bin/env python3
"""Check personal Facebook account status via Graph API.
Usage: check-account-status.py [user_id] [access_token]"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import fb_token, graph, show  # noqa: E402

user_id = sys.argv[1] if len(sys.argv) > 1 else "me"
token = fb_token(sys.argv[2] if len(sys.argv) > 2 else "")

print("Checking Facebook account status...\n")
show(graph(f"/{user_id}", {
    "access_token": token,
    "fields": "name,id,account_status,can_advertise,can_use_messenger"}))

print("""
=== Web-based checks ===
Account Status page: https://www.facebook.com/account_status
Account Quality page: https://www.facebook.com/accountquality
Business Support Home: https://www.facebook.com/business-support-home/""")
