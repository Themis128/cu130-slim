#!/usr/bin/env python3
"""Get any Instagram user's profile.
Usage: get-user.py <session_id> <username>"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import sc  # noqa: E402
from skill_http import usage  # noqa: E402

if len(sys.argv) < 3:
    usage("get-user.py <session_id> <username>")
sid, username = sys.argv[1], sys.argv[2]
d = sc("GET", f"/user?username={username}", sid=sid)
if isinstance(d, dict):
    for k in ("username", "full_name", "pk", "biography", "external_url",
              "follower_count", "following_count", "media_count",
              "category", "is_business", "is_verified"):
        print(f"{k.replace('_', ' ').title()}: {d.get(k, 'N/A')}")
else:
    print(d)
